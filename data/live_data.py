from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


STABLE_BASES = {
    "USDT", "USDC", "FDUSD", "TUSD", "BUSD", "USDP", "DAI", "EUR", "AEUR",
    "EURI", "PYUSD", "USD1", "BFUSD", "USDE", "USDS", "USTC",
}
LEVERAGED_MARKERS = ("UP", "DOWN", "BULL", "BEAR")


@dataclass
class UpdateStats:
    symbols: int
    rows_downloaded: int
    latest_crypto_date: date | None
    latest_sp500_date: date | None
    sp500_updated: bool
    sp500_source: str
    elapsed_seconds: float


class BinancePublicClient:
    def __init__(self, base_url: str, fallback_url: str, timeout: int = 20):
        self.base_urls = [base_url.rstrip("/"), fallback_url.rstrip("/")]
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "V4-Crypto-Monitor/0.3.2"})

    def _get(self, path: str, params: dict | None = None):
        last_exc = None
        for base in self.base_urls:
            try:
                r = self.session.get(base + path, params=params, timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except Exception as exc:  # pragma: no cover - depends on network
                last_exc = exc
        raise RuntimeError(f"Falha ao consultar Binance: {last_exc}")

    def exchange_info(self) -> dict:
        return self._get("/api/v3/exchangeInfo")

    def tickers_24h(self) -> list[dict]:
        return self._get("/api/v3/ticker/24hr")

    def klines(self, symbol: str, start_ms: int | None = None, end_ms: int | None = None,
               limit: int = 1000) -> list:
        params = {"symbol": symbol, "interval": "1d", "limit": limit}
        if start_ms is not None:
            params["startTime"] = int(start_ms)
        if end_ms is not None:
            params["endTime"] = int(end_ms)
        return self._get("/api/v3/klines", params=params)


def _base_asset(symbol: str) -> str:
    return symbol[:-4] if symbol.endswith("USDT") else symbol


def _eligible_symbol(symbol: str, info: dict) -> bool:
    if not symbol.endswith("USDT") or info.get("status") != "TRADING":
        return False
    if not info.get("isSpotTradingAllowed", True):
        return False
    base = info.get("baseAsset") or _base_asset(symbol)
    if base in STABLE_BASES:
        return False
    upper = base.upper()
    if any(upper.endswith(x) and len(upper) > len(x) + 1 for x in LEVERAGED_MARKERS):
        return False
    if upper.startswith(("WBTC", "WETH", "STETH", "WSTETH", "BETH")):
        return False
    return True


def discover_liquid_symbols(client: BinancePublicClient, universe_size: int) -> list[str]:
    info = client.exchange_info()
    info_by_symbol = {x["symbol"]: x for x in info.get("symbols", [])}
    tickers = client.tickers_24h()
    rows = []
    for t in tickers:
        symbol = t.get("symbol", "")
        meta = info_by_symbol.get(symbol)
        if not meta or not _eligible_symbol(symbol, meta):
            continue
        try:
            qv = float(t.get("quoteVolume", 0.0))
        except (TypeError, ValueError):
            qv = 0.0
        rows.append((qv, symbol))
    rows.sort(reverse=True)
    selected = [s for _, s in rows[:universe_size]]
    for core in ("BTCUSDT", "ETHUSDT"):
        if core not in selected and core in info_by_symbol:
            selected.append(core)
    return selected


def _klines_to_df(rows: list) -> pd.DataFrame:
    cols = [
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_base", "taker_quote", "ignore",
    ]
    if not rows:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "quote_volume"])
    df = pd.DataFrame(rows, columns=cols)
    df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.date
    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[["date", "open", "high", "low", "close", "volume", "quote_volume"]].dropna()


def _date_to_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def _load_listing_metadata(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_listing_metadata(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def update_crypto_cache(
    client: BinancePublicClient,
    cache_dir: Path,
    universe_size: int,
    history_days: int,
    progress: Callable[[int, int, str], None] | None = None,
) -> tuple[list[str], int, date | None]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    listing_path = cache_dir.parent / "listing_dates.json"
    listing = _load_listing_metadata(listing_path)

    symbols = discover_liquid_symbols(client, universe_size)
    today = datetime.now(timezone.utc).date()
    total_rows = 0
    latest = None

    for i, symbol in enumerate(symbols, 1):
        if progress:
            progress(i, len(symbols), symbol)

        if symbol not in listing:
            try:
                first = client.klines(symbol, start_ms=0, limit=1)
                if first:
                    listing[symbol] = datetime.fromtimestamp(first[0][0] / 1000, tz=timezone.utc).date().isoformat()
            except Exception:
                pass
            time.sleep(0.025)

        path = cache_dir / f"{symbol}.csv"
        old = pd.DataFrame()
        if path.exists():
            try:
                old = pd.read_csv(path, parse_dates=["date"])
                old["date"] = old["date"].dt.date
            except Exception:
                old = pd.DataFrame()

        if not old.empty:
            start = max(old["date"]) + timedelta(days=1)
        else:
            start = today - timedelta(days=history_days)

        if start <= today:
            try:
                new_rows = client.klines(symbol, start_ms=_date_to_ms(start), limit=1000)
                new = _klines_to_df(new_rows)
            except Exception:
                new = pd.DataFrame()
        else:
            new = pd.DataFrame()

        if not new.empty:
            total_rows += len(new)
            combined = new if old.empty else pd.concat([old, new], ignore_index=True)
            combined = combined.drop_duplicates("date", keep="last").sort_values("date")
            combined.to_csv(path, index=False)
        elif not old.empty:
            combined = old
        else:
            combined = pd.DataFrame()

        if not combined.empty:
            d = max(combined["date"])
            latest = d if latest is None else max(latest, d)
        time.sleep(0.025)

    _save_listing_metadata(listing_path, listing)
    (cache_dir.parent / "universe.json").write_text(
        json.dumps({"updated_at": datetime.now(timezone.utc).isoformat(), "symbols": symbols}, indent=2),
        encoding="utf-8",
    )
    return symbols, total_rows, latest


def _read_sp500_cache(destination: Path) -> tuple[pd.DataFrame, date | None]:
    if not destination.exists():
        return pd.DataFrame(columns=["date", "close"]), None
    try:
        df = pd.read_csv(destination)
        if "date" not in df.columns or "close" not in df.columns:
            date_col = "DATE" if "DATE" in df.columns else df.columns[0]
            value_col = "SP500" if "SP500" in df.columns else df.columns[1]
            df = df.rename(columns={date_col: "date", value_col: "close"})
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["date", "close"])[["date", "close"]].sort_values("date")
        latest = None if df.empty else df["date"].max().date()
        return df, latest
    except Exception:
        return pd.DataFrame(columns=["date", "close"]), None


def _previous_month_end(d: date) -> date:
    return date(d.year, d.month, 1) - timedelta(days=1)


def _sp500_cache_is_sufficient(destination: Path, reference_date: date | None = None) -> tuple[bool, date | None]:
    """Return True when the local S&P 500 copy is enough for this month's macro signal.

    The V4 regime is fixed for the whole month and uses the previous month-end.
    Because the S&P 500 does not trade on weekends/holidays, a last observation up to
    seven calendar days before the month-end is accepted. We require an observation at least 30 calendar days before the required month-end.
    The packaged bootstrap intentionally stores month-end observations only.
    """
    reference_date = reference_date or datetime.now(timezone.utc).date()
    required = _previous_month_end(reference_date)
    df, latest = _read_sp500_cache(destination)
    if df.empty or latest is None:
        return False, latest
    earliest = df["date"].min().date()
    has_history = earliest <= required - timedelta(days=30)
    covers_month_end = latest >= required - timedelta(days=7)
    return bool(has_history and covers_month_end), latest


def ensure_sp500_seed(destination: Path, seed_file: Path | None) -> tuple[date | None, bool]:
    """Install the packaged bootstrap only when no local cache exists."""
    if destination.exists():
        _, latest = _read_sp500_cache(destination)
        return latest, False
    if seed_file is None or not seed_file.exists():
        return None, False
    destination.parent.mkdir(parents=True, exist_ok=True)
    seed = pd.read_csv(seed_file)
    seed["date"] = pd.to_datetime(seed["date"], errors="coerce")
    seed["close"] = pd.to_numeric(seed["close"], errors="coerce")
    seed = seed.dropna(subset=["date", "close"])[["date", "close"]].sort_values("date")
    seed.to_csv(destination, index=False)
    latest = None if seed.empty else seed["date"].max().date()
    return latest, True


def _merge_sp500(destination: Path, incoming: pd.DataFrame) -> date:
    incoming = incoming.copy()
    incoming["date"] = pd.to_datetime(incoming["date"], errors="coerce")
    incoming["close"] = pd.to_numeric(incoming["close"], errors="coerce")
    incoming = incoming.dropna(subset=["date", "close"])[["date", "close"]]
    if incoming.empty:
        raise RuntimeError("A fonte respondeu sem observações válidas do S&P 500.")
    old, _ = _read_sp500_cache(destination)
    if not old.empty:
        incoming = pd.concat([old, incoming], ignore_index=True)
    incoming = incoming.drop_duplicates("date", keep="last").sort_values("date")
    tmp = destination.with_suffix(".tmp.csv")
    incoming.to_csv(tmp, index=False)
    tmp.replace(destination)
    return incoming["date"].max().date()


def _download_fred(url: str, start: date, end: date, timeout: int) -> pd.DataFrame:
    sep = "&" if "?" in url else "?"
    request_url = f"{url}{sep}cosd={start.isoformat()}&coed={end.isoformat()}"
    session = requests.Session()
    retry = Retry(total=2, connect=2, read=2, backoff_factor=1.0,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET"]), raise_on_status=False)
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": "V4-Crypto-Monitor/0.3.2"})
    r = session.get(request_url, timeout=(10, timeout))
    r.raise_for_status()
    from io import StringIO
    df = pd.read_csv(StringIO(r.text))
    date_col = "DATE" if "DATE" in df.columns else df.columns[0]
    value_col = "SP500" if "SP500" in df.columns else df.columns[1]
    return df.rename(columns={date_col: "date", value_col: "close"})[["date", "close"]]


def _download_stooq(url: str, start: date, end: date, timeout: int) -> pd.DataFrame:
    sep = "&" if "?" in url else "?"
    request_url = f"{url}{sep}d1={start.strftime('%Y%m%d')}&d2={end.strftime('%Y%m%d')}"
    r = requests.get(request_url, timeout=(10, timeout), headers={"User-Agent": "Mozilla/5.0 V4-Crypto-Monitor/0.3.2"})
    r.raise_for_status()
    from io import StringIO
    df = pd.read_csv(StringIO(r.text))
    lower = {c.lower(): c for c in df.columns}
    if "date" not in lower or "close" not in lower:
        raise RuntimeError("Stooq respondeu em formato inesperado.")
    return df.rename(columns={lower["date"]: "date", lower["close"]: "close"})[["date", "close"]]


def update_sp500_cache(
    fred_url: str,
    destination: Path,
    reference_date: date | None = None,
    force: bool = False,
    timeout: int = 30,
    fallback_url: str | None = None,
    seed_file: Path | None = None,
) -> tuple[date | None, bool, str]:
    """Refresh the monthly S&P copy only when necessary, with FRED -> Stooq fallback."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    reference_date = reference_date or datetime.now(timezone.utc).date()
    ensure_sp500_seed(destination, seed_file)
    sufficient, latest = _sp500_cache_is_sufficient(destination, reference_date)
    if sufficient and not force:
        return latest, False, "cache local"

    end = _previous_month_end(reference_date)
    start = end - timedelta(days=120)
    errors = []
    sources = [("FRED", lambda: _download_fred(fred_url, start, end, timeout))]
    if fallback_url:
        sources.append(("Stooq", lambda: _download_stooq(fallback_url, start, end, timeout)))
    for name, loader in sources:
        try:
            latest = _merge_sp500(destination, loader())
            return latest, True, name
        except Exception as exc:  # network dependent
            errors.append(f"{name}: {exc}")

    # Never delete a valid cache. If it is enough for the current monthly signal,
    # the application keeps operating normally. If stale, the live engine carries
    # forward the last valid macro reference and labels it as such.
    _, latest = _read_sp500_cache(destination)
    if latest is not None:
        return latest, False, "cache local (fontes indisponíveis)"
    raise RuntimeError("Não foi possível obter S&P 500. " + " | ".join(errors))


def update_crypto_only(config, progress: Callable[[int, int, str], None] | None = None) -> UpdateStats:
    started = time.time()
    client = BinancePublicClient(config.binance_base_url, config.binance_fallback_url)
    symbols, rows, crypto_date = update_crypto_cache(client, config.crypto_cache_dir,
        config.live_universe_size, config.live_history_days, progress)
    ensure_sp500_seed(config.sp500_cache_file, getattr(config, "sp500_seed_file", None))
    _, sp_date = _read_sp500_cache(config.sp500_cache_file)
    return UpdateStats(len(symbols), rows, crypto_date, sp_date, False, "não consultado", time.time()-started)


def update_sp500_for_config(config, force: bool = False) -> tuple[date | None, bool, str]:
    return update_sp500_cache(config.fred_csv_url, config.sp500_cache_file,
        force=force, fallback_url=getattr(config, "stooq_csv_url", None),
        seed_file=getattr(config, "sp500_seed_file", None))

def update_all(config, progress: Callable[[int, int, str], None] | None = None) -> UpdateStats:
    """Backward-compatible full refresh; UI v0.3.2 uses separate buttons."""
    stats = update_crypto_only(config, progress)
    sp_date, sp_updated, sp_source = update_sp500_for_config(config)
    stats.latest_sp500_date = sp_date
    stats.sp500_updated = sp_updated
    stats.sp500_source = sp_source
    return stats

def cache_status(config) -> dict:
    ensure_sp500_seed(config.sp500_cache_file, getattr(config, "sp500_seed_file", None))
    symbols = []
    upath = config.cache_dir / "universe.json"
    if upath.exists():
        try:
            symbols = json.loads(upath.read_text(encoding="utf-8")).get("symbols", [])
        except Exception:
            symbols = []

    # Common last date across the current universe is safer than the maximum date.
    crypto_dates = []
    for symbol in symbols:
        path = config.crypto_cache_dir / f"{symbol}.csv"
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, usecols=["date"])
            if not df.empty:
                crypto_dates.append(pd.to_datetime(df["date"]).max().date())
        except Exception:
            pass
    latest_crypto = min(crypto_dates) if crypto_dates and len(crypto_dates) == len(symbols) else None

    _, latest_sp = _read_sp500_cache(config.sp500_cache_file)
    today = datetime.now(timezone.utc).date()
    expected_crypto = today - timedelta(days=1)
    crypto_current = bool(latest_crypto and latest_crypto >= expected_crypto)
    sp_current, _ = _sp500_cache_is_sufficient(config.sp500_cache_file, today)
    return {
        "symbols": len(symbols), "latest_crypto": latest_crypto, "latest_sp500": latest_sp,
        "crypto_current": crypto_current, "sp500_current": bool(sp_current),
        "expected_crypto_date": expected_crypto,
        "ready": bool(symbols and latest_crypto and latest_sp),
    }

