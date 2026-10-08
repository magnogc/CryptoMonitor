from datetime import date
from pathlib import Path

import pandas as pd

from data.live_data import _sp500_cache_is_sufficient, ensure_sp500_seed, cache_status


def test_sp500_cache_sufficient_for_whole_month(tmp_path: Path):
    p = tmp_path / "sp500.csv"
    dates = pd.date_range("2026-04-01", "2026-08-31", freq="B")
    pd.DataFrame({"date": dates, "close": range(len(dates))}).to_csv(p, index=False)
    ok, latest = _sp500_cache_is_sufficient(p, date(2026, 9, 19))
    assert ok
    assert latest == date(2026, 8, 31)


def test_sp500_cache_stale_next_month(tmp_path: Path):
    p = tmp_path / "sp500.csv"
    dates = pd.date_range("2026-04-01", "2026-08-31", freq="B")
    pd.DataFrame({"date": dates, "close": range(len(dates))}).to_csv(p, index=False)
    ok, _ = _sp500_cache_is_sufficient(p, date(2026, 10, 10))
    assert not ok


def test_seed_installs_when_cache_missing(tmp_path: Path):
    seed = tmp_path / "seed.csv"
    dest = tmp_path / "cache" / "sp500.csv"
    pd.DataFrame({"date":["2026-07-31","2026-08-31"],"close":[7489.72,7686.14]}).to_csv(seed,index=False)
    latest, installed = ensure_sp500_seed(dest, seed)
    assert installed
    assert latest == date(2026,8,31)
    assert dest.exists()


def test_seed_merges_newer_month_into_existing_cache(tmp_path: Path):
    seed = tmp_path / "seed.csv"
    dest = tmp_path / "cache" / "sp500.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "date": ["2026-07-31", "2026-08-31"],
        "close": [7489.72, 7686.14],
    }).to_csv(dest, index=False)
    pd.DataFrame({
        "date": ["2026-08-31", "2026-09-30"],
        "close": [7686.14, 7651.54],
    }).to_csv(seed, index=False)

    latest, changed = ensure_sp500_seed(dest, seed)

    assert changed
    assert latest == date(2026, 9, 30)
    merged = pd.read_csv(dest)
    assert "2026-09-30" in set(merged["date"].astype(str))
