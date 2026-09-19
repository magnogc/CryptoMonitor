from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .indicators import adx, atr, awesome_oscillator, ema, macd, pct_rank, rsi, stochastic
from .models import OpportunityState, PositionTarget, WeeklyResult
from .rebalance import classify_action


def latest_friday(d: date) -> date:
    return d - timedelta(days=(d.weekday() - 4) % 7)


def previous_month_end(d: date) -> date:
    first = date(d.year, d.month, 1)
    return first - timedelta(days=1)


def _month_diff(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def _weights(regime: str, alts: list[str]) -> dict[str, float]:
    if regime == "defensive":
        return {"USDT": 1.0}
    if regime == "neutral":
        return {"BTC": 0.10, "ETH": 0.10, "USDT": 0.80}
    assets = ["BTC", "ETH"] + list(dict.fromkeys(alts))
    w = 1.0 / len(assets)
    return {x: w for x in assets}


def _asof(df: pd.DataFrame, d: date) -> pd.DataFrame:
    return df[df["date"] <= d].copy()


def _close_asof(df: pd.DataFrame, d: date) -> float | None:
    x = _asof(df, d)
    if x.empty:
        return None
    return float(x.iloc[-1]["close"])


def _ret_between(df: pd.DataFrame, d: date, days: int) -> float | None:
    x = _asof(df, d)
    if len(x) < 2:
        return None
    end = float(x.iloc[-1]["close"])
    target = d - timedelta(days=days)
    old = x[x["date"] <= target]
    if old.empty:
        old = x.iloc[:1]
    start = float(old.iloc[-1]["close"])
    return end / start - 1 if start else None


@dataclass
class CoreSelection:
    analysis_date: date
    regime: str
    candidates: list[str]
    confirmed: list[str]
    baseline: list[str]
    confirmation_count: int
    universe: pd.DataFrame
    signals: pd.DataFrame
    macro: dict


class LiveV4Engine:
    """
    Live/forward implementation of the frozen V4 architecture.

    Design choices intentionally mirror the research documentation:
    - monthly macro regime;
    - monthly structural eligibility;
    - V4 continuation + correction/acceleration pivot;
    - weekly review, change only after two consecutive equal ordered Top3 lists;
    - equal weights in Risk On.

    The engine is deterministic from cached market data and never sends orders.
    """

    def __init__(self, config):
        self.config = config
        self.series = self._load_series()
        self.listing = self._load_listing_dates()
        self.sp500 = self._load_sp500()

    def _load_listing_dates(self) -> dict[str, date]:
        p = self.config.cache_dir / "listing_dates.json"
        if not p.exists():
            return {}
        raw = json.loads(p.read_text(encoding="utf-8"))
        out = {}
        for k, v in raw.items():
            try:
                out[k] = date.fromisoformat(v)
            except Exception:
                pass
        return out

    def _load_series(self) -> dict[str, pd.DataFrame]:
        out = {}
        if not self.config.crypto_cache_dir.exists():
            return out
        for p in self.config.crypto_cache_dir.glob("*USDT.csv"):
            try:
                df = pd.read_csv(p, parse_dates=["date"])
                df["date"] = df["date"].dt.date
                df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
                if len(df) >= 40:
                    out[p.stem] = df
            except Exception:
                continue
        return out

    def _load_sp500(self) -> pd.DataFrame:
        p = self.config.sp500_cache_file
        if not p.exists():
            return pd.DataFrame(columns=["date", "close"])
        df = pd.read_csv(p, parse_dates=["date"])
        df["date"] = df["date"].dt.date
        return df.dropna().sort_values("date").reset_index(drop=True)

    @property
    def ready(self) -> bool:
        return "BTCUSDT" in self.series and "ETHUSDT" in self.series and not self.sp500.empty

    @property
    def max_date(self) -> date | None:
        if not self.ready:
            return None
        # S&P 500 is used only for the previous completed month's macro signal.
        # Its cache is intentionally refreshed monthly, so it must not cap the
        # weekly crypto reference date.
        return max(self.series["BTCUSDT"]["date"])

    def macro_regime(self, analysis_date: date) -> dict:
        # The regime is fixed for the whole month using the last completed
        # monthly observation. This reproduces the V3/V4 three-state rule:
        # BTC 1m > S&P 1m => Risk On; otherwise BTC 3m>0 => Neutral; else Defensive.
        requested_signal_date = previous_month_end(analysis_date)
        latest_sp = max(self.sp500["date"]) if not self.sp500.empty else requested_signal_date
        # If both external S&P sources are unavailable at a month change, carry
        # forward the last valid macro reference instead of blocking the app.
        signal_date = min(requested_signal_date, latest_sp)
        btc = self.series["BTCUSDT"]
        btc_30 = _ret_between(btc, signal_date, 30)
        btc_90 = _ret_between(btc, signal_date, 90)
        sp_30 = _ret_between(self.sp500, signal_date, 30)
        if btc_30 is None or sp_30 is None or btc_90 is None:
            raise RuntimeError("Histórico insuficiente para classificar o regime macro.")
        if btc_30 > sp_30:
            regime = "risk_on"
        elif btc_90 > 0:
            regime = "neutral"
        else:
            regime = "defensive"
        return {
            "signal_date": signal_date,
            "requested_signal_date": requested_signal_date,
            "macro_stale": signal_date < requested_signal_date,
            "btc_30d": btc_30,
            "sp500_30d": sp_30,
            "btc_90d": btc_90,
            "regime": regime,
        }

    def structural_universe(self, analysis_date: date) -> pd.DataFrame:
        cutoff = previous_month_end(analysis_date)
        # Twelve completed monthly buckets ending at cutoff.
        month_rows = []
        for symbol, df in self.series.items():
            if symbol in {"BTCUSDT", "ETHUSDT"}:
                continue
            x = df[df["date"] <= cutoff].copy()
            if x.empty:
                continue
            x["month"] = pd.PeriodIndex(pd.to_datetime(x["date"]), freq="M")
            monthly = x.groupby("month", as_index=False)["quote_volume"].sum().tail(12)
            for _, row in monthly.iterrows():
                month_rows.append((symbol, str(row["month"]), float(row["quote_volume"])))
        if not month_rows:
            return pd.DataFrame()
        q = pd.DataFrame(month_rows, columns=["symbol", "month", "quote_volume"])
        q["rank"] = q.groupby("month")["quote_volume"].rank(ascending=False, method="min")
        q["top30"] = q["rank"] <= self.config.top_liquidity_n
        months = sorted(q["month"].unique())[-12:]
        current_month = months[-1]
        rows = []
        for symbol, g in q[q["month"].isin(months)].groupby("symbol"):
            listing = self.listing.get(symbol)
            if listing is None:
                continue
            age = _month_diff(listing, cutoff)
            gg = g.set_index("month").reindex(months)
            top = gg["top30"].fillna(False).astype(bool)
            presence = int(top.sum())
            seq = 0
            for v in reversed(top.tolist()):
                if v:
                    seq += 1
                else:
                    break
            ranks = gg.loc[top, "rank"].dropna()
            median_rank = float(ranks.median()) if not ranks.empty else 31.0
            age_score = min(age / 36.0, 1.0)
            persistence = presence / max(1, len(months))
            rank_quality = max(0.0, min(1.0, (31.0 - median_rank) / 30.0))
            continuity = min(seq / max(1, len(months)), 1.0)
            solidity = 0.25*age_score + 0.35*persistence + 0.25*rank_quality + 0.15*continuity
            current_rank = gg.loc[current_month, "rank"] if current_month in gg.index else np.nan
            eligible = (
                age >= self.config.structural_age_months
                and presence >= self.config.structural_presence_months
                and seq >= self.config.structural_sequence_months
                and solidity >= self.config.structural_score_min
                and bool(top.iloc[-1])
            )
            rows.append({
                "asset": symbol[:-4], "symbol": symbol, "age_months": age,
                "top30_12m": presence, "current_sequence": seq,
                "median_rank": median_rank, "current_rank": current_rank,
                "solidity": solidity, "eligible": eligible,
            })
        out = pd.DataFrame(rows)
        if out.empty:
            return out
        return out.sort_values(["eligible", "solidity", "current_rank"], ascending=[False, False, True]).reset_index(drop=True)

    def _technical_row(self, symbol: str, asof: date, btc: pd.DataFrame, eth: pd.DataFrame) -> dict | None:
        df = _asof(self.series[symbol], asof).copy().tail(180).reset_index(drop=True)
        if len(df) < 70:
            return None
        close = df["close"]
        e10, e20, e50 = ema(close, 10), ema(close, 20), ema(close, 50)
        a14 = atr(df, 14)
        adx14, pdi, mdi = adx(df, 14)
        mline, msignal, mh = macd(close)
        ao = awesome_oscillator(df)
        rs = rsi(close, 14)
        sk, sd = stochastic(df)

        # Weekly persistent relative strength over the last 8 weekly observations.
        alt_w = df.set_index(pd.to_datetime(df["date"]))["close"].resample("W-FRI").last().dropna()
        btc_w = _asof(btc, asof).set_index(pd.to_datetime(_asof(btc, asof)["date"]))["close"].resample("W-FRI").last().dropna()
        eth_w = _asof(eth, asof).set_index(pd.to_datetime(_asof(eth, asof)["date"]))["close"].resample("W-FRI").last().dropna()
        ww = pd.concat([alt_w.pct_change(), btc_w.pct_change(), eth_w.pct_change()], axis=1, join="inner").dropna().tail(8)
        rs_persist = float(((ww.iloc[:,0] > ww.iloc[:,1]) & (ww.iloc[:,0] > ww.iloc[:,2])).mean()) if len(ww) else 0.0

        # HH/HL: fraction of the last eight weekly bars with both high and low above previous week.
        dd = df.copy()
        dd.index = pd.to_datetime(dd["date"])
        wk = dd[["high","low"]].resample("W-FRI").agg({"high":"max","low":"min"}).dropna().tail(9)
        hhhl = float(((wk["high"] > wk["high"].shift()) & (wk["low"] > wk["low"].shift())).iloc[1:].mean()) if len(wk) >= 2 else 0.0

        ma_conditions = [
            close.iloc[-1] > e20.iloc[-1],
            e20.iloc[-1] > e50.iloc[-1],
            e20.iloc[-1] > e20.iloc[-11],
            e50.iloc[-1] > e50.iloc[-11],
        ]
        ma_score = float(np.mean(ma_conditions))
        adx_norm = float(np.clip((adx14.iloc[-1]-15)/25, 0, 1)) if pd.notna(adx14.iloc[-1]) else 0.0
        adx_vote = bool(adx_norm >= 0.40 and pdi.iloc[-1] > mdi.iloc[-1])
        macd_vote = bool(mh.iloc[-1] > 0)
        votes = int(rs_persist >= 0.50) + int(hhhl >= 0.50) + int(ma_score >= 0.75) + int(adx_vote) + int(macd_vote)

        below20_recent = bool((close.tail(10).reset_index(drop=True) < e20.tail(10).reset_index(drop=True)).any())
        dd20 = float(close.iloc[-1] / close.tail(20).max() - 1)
        correction = below20_recent or dd20 <= -0.08
        recovered = bool(close.iloc[-1] > e10.iloc[-1])
        ext = float((close.iloc[-1]-e20.iloc[-1])/a14.iloc[-1]) if pd.notna(a14.iloc[-1]) and a14.iloc[-1] else np.nan

        ao_turn = bool(ao.iloc[-1] > ao.iloc[-2]) if len(ao.dropna()) >= 2 else False
        macd_acc = bool(mh.iloc[-1] > mh.iloc[-2]) if len(mh.dropna()) >= 2 else False
        rsi_turn = bool(rs.iloc[-1] > rs.iloc[-4]) if len(rs.dropna()) >= 4 else False
        stoch_turn = bool(sk.iloc[-1] > sd.iloc[-1] and sk.iloc[-2] <= sd.iloc[-2] and sk.iloc[-3:].min() < 30) if len(sk.dropna()) >= 3 else False
        accel = int(ao_turn) + int(macd_acc) + int(rsi_turn) + int(stoch_turn)
        continuation = votes >= 3
        pivot = bool(correction and recovered and accel >= 2 and votes >= 1 and pd.notna(ext) and ext <= 2.0)
        # Setup confidence: normalized rule satisfaction, used only to order eligible setups.
        cont_conf = votes / 5.0 if continuation else 0.0
        pivot_conf = (0.45*(accel/4.0) + 0.25*min(votes/3.0,1.0) + 0.15*float(recovered) + 0.15*float(correction)) if pivot else 0.0
        confidence = max(cont_conf, pivot_conf)
        return {
            "asset": symbol[:-4], "symbol": symbol, "rs_persist": rs_persist, "hhhl": hhhl,
            "ma_score": ma_score, "adx": float(adx14.iloc[-1]), "plus_di": float(pdi.iloc[-1]),
            "minus_di": float(mdi.iloc[-1]), "adx_vote": adx_vote, "macd_vote": macd_vote,
            "trend_votes": votes, "correction": correction, "dd20": dd20, "recovered_ema10": recovered,
            "extension_atr": ext, "ao_turn": ao_turn, "macd_accel": macd_acc, "rsi_turn": rsi_turn,
            "stoch_turn": stoch_turn, "accel_votes": accel, "pivot": pivot, "continuation": continuation,
            "setup_confidence": confidence,
        }

    def weekly_candidates(self, analysis_date: date, universe: pd.DataFrame | None = None) -> tuple[list[str], pd.DataFrame]:
        if universe is None:
            universe = self.structural_universe(analysis_date)
        if universe.empty:
            return [], pd.DataFrame()
        elig = universe[universe["eligible"]].copy()
        btc, eth = self.series["BTCUSDT"], self.series["ETHUSDT"]
        rows = []
        for _, u in elig.iterrows():
            symbol = str(u["symbol"])
            if symbol not in self.series:
                continue
            tr = self._technical_row(symbol, analysis_date, btc, eth)
            if tr is None:
                continue
            tr["solidity"] = float(u["solidity"])
            tr["current_rank"] = float(u["current_rank"])
            rows.append(tr)
        sig = pd.DataFrame(rows)
        if sig.empty:
            return [], sig
        candidates = sig[sig["continuation"] | sig["pivot"]].copy()
        if candidates.empty:
            return [], sig
        candidates["liq_quality"] = 1 - (candidates["current_rank"]-1) / max(1, self.config.top_liquidity_n-1)
        candidates = candidates.sort_values(
            ["setup_confidence", "solidity", "liq_quality"], ascending=[False, False, False]
        )
        return candidates["asset"].head(3).tolist(), sig.sort_values("setup_confidence", ascending=False)

    def confirmed_selection(self, analysis_date: date) -> CoreSelection:
        macro = self.macro_regime(analysis_date)
        universe = self.structural_universe(analysis_date)
        if macro["regime"] != "risk_on":
            return CoreSelection(analysis_date, macro["regime"], [], [], [], 0, universe, pd.DataFrame(), macro)

        month_start = date(analysis_date.year, analysis_date.month, 1)
        baseline_date = previous_month_end(analysis_date)
        baseline, baseline_sig = self.weekly_candidates(baseline_date, universe)
        confirmed = baseline
        prev_candidate = None
        count = 0
        latest_candidate = baseline
        latest_sig = baseline_sig

        d = month_start
        fridays = []
        while d <= analysis_date:
            if d.weekday() == 4:
                fridays.append(d)
            d += timedelta(days=1)
        for f in fridays:
            candidate, sig = self.weekly_candidates(f, universe)
            latest_candidate, latest_sig = candidate, sig
            if candidate == prev_candidate:
                count += 1
            else:
                prev_candidate = candidate
                count = 1
            if count >= 2 and candidate != confirmed:
                confirmed = candidate
        return CoreSelection(
            analysis_date=analysis_date, regime=macro["regime"], candidates=latest_candidate,
            confirmed=confirmed, baseline=baseline, confirmation_count=count,
            universe=universe, signals=latest_sig, macro=macro,
        )

    def _opportunity_candidates(self, analysis_date: date, regime: str) -> pd.DataFrame:
        if regime != "risk_on":
            return pd.DataFrame()
        btc = _asof(self.series["BTCUSDT"], analysis_date)
        btc_r28 = _ret_between(btc, analysis_date, 28) or 0.0
        rows = []
        for symbol, source in self.series.items():
            if symbol in {"BTCUSDT", "ETHUSDT"}:
                continue
            df = _asof(source, analysis_date).copy().tail(180).reset_index(drop=True)
            if len(df) < 45:
                continue
            close = df["close"]
            e10, e20 = ema(close,10), ema(close,20)
            a14 = atr(df,14)
            adx14,pdi,mdi = adx(df,14)
            _,_,mh = macd(close)
            ao = awesome_oscillator(df)
            rr = rsi(close,14)
            r14 = close.iloc[-1]/close.iloc[-15]-1
            r28 = close.iloc[-1]/close.iloc[-29]-1 if len(close)>=29 else np.nan
            rs28 = r28 - btc_r28
            rs7 = close.iloc[-1]/close.iloc[-8]-1
            btc7 = _ret_between(btc, analysis_date, 7) or 0.0
            rs_acc = (rs7-btc7) - (rs28/4.0)
            dd30 = close.iloc[-1]/close.tail(30).max()-1
            ext = (close.iloc[-1]-e20.iloc[-1])/a14.iloc[-1] if a14.iloc[-1] else np.nan
            low_recent = df["low"].tail(7).min()
            low_prior = df["low"].iloc[-14:-7].min()
            higher_low = (low_recent-low_prior)/a14.iloc[-1] if a14.iloc[-1] else np.nan
            macd_slope = (mh.iloc[-1]-mh.iloc[-4]) / max(abs(close.iloc[-1]),1e-12)
            ao_pct = abs(ao.iloc[-1])/max(abs(close.iloc[-1]),1e-12)
            macd2 = mh.iloc[-1] > mh.iloc[-2] > mh.iloc[-3]
            base = (
                dd30 <= -0.07 and close.iloc[-1] > e20.iloc[-1] and pd.notna(ext) and ext <= 1.5
                and higher_low > 0 and rs_acc > 0 and mh.iloc[-1] > mh.iloc[-4]
                and 42 <= rr.iloc[-1] <= 68 and rr.iloc[-1] > rr.iloc[-4] and r14 <= 0.25
                and adx14.iloc[-1] <= 25 and ao_pct <= 0.03 and macd2
            )
            if not base:
                continue
            # BBWidth quality: compression relative to 120d then expansion.
            sma20=close.rolling(20).mean(); sd20=close.rolling(20).std(); bbw=4*sd20/sma20
            recent_min=bbw.tail(10).min(); hist=bbw.tail(120).dropna()
            bb_q = float((hist >= recent_min).mean()) if len(hist) else 0.0
            if len(bbw.dropna()) >= 6 and bbw.iloc[-1] > bbw.iloc[-6]:
                bb_q = min(1.0, bb_q + 0.15)
            rows.append({
                "asset":symbol[:-4],"symbol":symbol,"rs_accel":rs_acc,"bb_quality":bb_q,
                "macd_slope":macd_slope,"extension_atr":ext,"higher_low_atr":higher_low,
                "adx":float(adx14.iloc[-1]),"ao_pct":ao_pct,"close":float(close.iloc[-1]),
            })
        out=pd.DataFrame(rows)
        if out.empty:
            return out
        out["rs_q"] = pct_rank(out["rs_accel"])
        out["bb_q"] = out["bb_quality"].clip(0,1)
        out["macd_q"] = pct_rank(out["macd_slope"])
        out["ext_q"] = pct_rank(-out["extension_atr"].abs())
        out["hl_q"] = pct_rank(out["higher_low_atr"])
        out["adx_q"] = pct_rank(-out["adx"])
        out["ao_q"] = pct_rank(-out["ao_pct"])
        out["score"] = (0.20*out.rs_q + 0.20*out.bb_q + 0.20*out.macd_q + 0.15*out.ext_q
                        + 0.15*out.hl_q + 0.05*out.adx_q + 0.05*out.ao_q)
        return out.sort_values("score",ascending=False).reset_index(drop=True)

    def opportunity_state(self, analysis_date: date, regime: str) -> OpportunityState:
        cand = self._opportunity_candidates(analysis_date, regime)
        if cand.empty:
            return OpportunityState(active=False)
        r=cand.iloc[0]
        return OpportunityState(
            active=True, asset=str(r.asset), score=float(r.score), entry_date=analysis_date+timedelta(days=1),
            exit_date=None, realized_return=None, status="Candidato hipotético V4-O — NÃO GERA ORDEM",
            components={
                "RS aceleração":float(r.rs_q),"BBWidth":float(r.bb_q),"MACD slope":float(r.macd_q),
                "Baixa extensão":float(r.ext_q),"Higher low":float(r.hl_q),"ADX jovem":float(r.adx_q),
                "AO pouco estendido":float(r.ao_q),
            },
        )

    def result_for(self, selected_date: date) -> WeeklyResult:
        if not self.ready:
            raise RuntimeError("Cache ao vivo ainda não está pronto. Atualize os dados primeiro.")
        maxd=self.max_date
        data_date=min(selected_date,maxd)
        analysis=latest_friday(data_date)
        previous=analysis-timedelta(days=7)
        current_sel=self.confirmed_selection(analysis)
        previous_sel=self.confirmed_selection(previous)
        target=_weights(current_sel.regime,current_sel.confirmed)
        current=_weights(previous_sel.regime,previous_sel.confirmed)
        assets=list(dict.fromkeys(["BTC","ETH"]+[x for x in current if x not in {"BTC","ETH","USDT"}]+[x for x in target if x not in {"BTC","ETH","USDT"}]+["USDT"]))
        positions=[]
        for asset in assets:
            cw=float(current.get(asset,0)); tw=float(target.get(asset,0)); act=classify_action(cw,tw)
            reason="Sem alteração nesta revisão"
            if act!="Manter":
                if current_sel.regime!=previous_sel.regime:
                    reason=f"Mudança de regime para {current_sel.regime.replace('_',' ').title()}"
                elif current_sel.confirmed!=previous_sel.confirmed:
                    reason="Seleção semanal confirmada em 2 revisões"
                else:
                    reason="Rebalanceamento V4 Core"
            positions.append(PositionTarget(asset,cw,tw,act,reason))
        changes=[]
        changed=[p for p in positions if p.action!="Manter"]
        if changed:
            changes.append(f"{len(changed)} alteração(ões) de peso na V4 Core nesta revisão.")
        else:
            changes.append("Nenhuma alteração operacional na V4 Core nesta revisão.")
        changes.append(f"Regime vigente: {current_sel.regime.replace('_',' ').title()}.")
        if current_sel.regime=="risk_on":
            changes.append("Seleção confirmada: "+(", ".join(current_sel.confirmed) if current_sel.confirmed else "sem altcoins."))
            changes.append(f"Candidato desta sexta: {', '.join(current_sel.candidates) if current_sel.candidates else 'sem altcoins'} · persistência {current_sel.confirmation_count}x.")
        opp=self.opportunity_state(analysis,current_sel.regime)
        changes.append(f"V4-O monitora {opp.asset}; sem ordem." if opp.active else "V4-O sem oportunidade nesta revisão.")
        latest_event={
            "effective_date":analysis+timedelta(days=1),"event_type":"live semanal",
            "regime":current_sel.regime,"altcoins":current_sel.confirmed,"turnover":0.5*sum(abs(target.get(x,0)-current.get(x,0)) for x in set(target)|set(current)),
            "cost":0.0,"monthly_signal_date":current_sel.macro["signal_date"],"monthly_original_selection":current_sel.baseline,
            "macro":current_sel.macro,"candidate_selection":current_sel.candidates,"confirmation_count":current_sel.confirmation_count,
            "eligible_count":int(current_sel.universe["eligible"].sum()) if not current_sel.universe.empty else 0,
        }
        return WeeklyResult(
            selected_date=selected_date,data_date=data_date,analysis_date=analysis,execution_date=analysis+timedelta(days=1),
            next_analysis_date=analysis+timedelta(days=7),next_execution_date=analysis+timedelta(days=8),regime=current_sel.regime,
            positions=positions,changes=changes,opportunity=opp,latest_event=latest_event,core_nav=None,shadow_nav=None,
        )

    def diagnostics(self, analysis_date: date) -> CoreSelection:
        return self.confirmed_selection(analysis_date)

    def save_snapshot(self, result: WeeklyResult) -> None:
        self.config.cache_dir.mkdir(parents=True,exist_ok=True)
        def conv(obj):
            if isinstance(obj,(date,datetime)): return obj.isoformat()
            if isinstance(obj,np.generic): return obj.item()
            raise TypeError
        payload=asdict(result)
        payload["saved_at"]=datetime.now(timezone.utc).isoformat()
        txt=json.dumps(payload,ensure_ascii=False,indent=2,default=conv)
        self.config.live_snapshot_file.write_text(txt,encoding="utf-8")
        with self.config.live_history_file.open("a",encoding="utf-8") as f:
            f.write(json.dumps(payload,ensure_ascii=False,default=conv)+"\n")
