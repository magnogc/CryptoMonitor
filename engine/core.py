from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .models import PositionTarget, WeeklyResult, OpportunityState
from .rebalance import classify_action

def _parse_assets(value: str) -> list[str]:
    if not value or str(value).strip() in {"", "—", "nan"}:
        return []
    return [x.strip() for x in str(value).split(",") if x.strip()]

def weights_from_state(regime: str, altcoins: str) -> dict[str, float]:
    """
    Frozen V4 Core allocation logic used in the backtest:
    - risk_on: equal weight among BTC, ETH and selected established alts;
    - neutral: 10% BTC + 10% ETH + 80% USDT;
    - defensive: 100% USDT.
    """
    if regime == "defensive":
        return {"USDT": 1.0}
    if regime == "neutral":
        return {"BTC": 0.10, "ETH": 0.10, "USDT": 0.80}

    assets = ["BTC", "ETH"] + _parse_assets(altcoins)
    assets = list(dict.fromkeys(assets))
    w = 1.0 / len(assets)
    return {a: w for a in assets}

def _latest_friday(d: date) -> date:
    return d - timedelta(days=(d.weekday() - 4) % 7)

def _next_friday(d: date) -> date:
    x = _latest_friday(d)
    if x <= d:
        x += timedelta(days=7)
    return x

class HistoricalV4Engine:
    """
    Real historical replay of the frozen V4 Core / V4-O research outputs.

    This engine DOES NOT fabricate future signals. It reads the exact event log
    of the selected V4 Core architecture (weekly + 2 confirmations) and the
    frozen V4-O opportunity history produced by the research workbooks.

    It is intentionally separated from a future live-data adapter. When new
    market data are connected, the UI contract can remain unchanged.
    """

    def __init__(self, data_dir: str | Path):
        data_dir = Path(data_dir)
        self.events = pd.read_csv(data_dir / "v4_core_events.csv", parse_dates=[
            "effective_date", "monthly_signal_date"
        ])
        self.performance = pd.read_csv(data_dir / "v4_performance_weekly.csv", parse_dates=["date"])
        self.opportunities = pd.read_csv(data_dir / "v4_opportunities.csv", parse_dates=[
            "entry_date", "exit_date"
        ])
        self.events = self.events.sort_values("effective_date").reset_index(drop=True)
        self.performance = self.performance.sort_values("date").reset_index(drop=True)
        self.opportunities = self.opportunities.sort_values("entry_date").reset_index(drop=True)

    @property
    def min_date(self) -> date:
        return self.performance["date"].min().date()

    @property
    def max_date(self) -> date:
        return self.performance["date"].max().date()

    def _state_at(self, d: date) -> pd.Series:
        df = self.events[self.events["effective_date"].dt.date <= d]
        if df.empty:
            return pd.Series({
                "effective_date": pd.Timestamp(self.min_date),
                "event_type": "inicial",
                "regime": "risk_on",
                "altcoins": "",
                "turnover": 0.0,
                "cost": 0.0,
                "monthly_signal_date": pd.NaT,
                "monthly_original_selection": "",
            })
        return df.iloc[-1]

    def _previous_state(self, state: pd.Series) -> pd.Series:
        try:
            idx = int(state.name)
        except (TypeError, ValueError):
            idx = -1
        if idx <= 0:
            return state
        return self.events.iloc[idx - 1]

    def _events_in_review_window(self, analysis_date: date, data_date: date) -> pd.DataFrame:
        # Historical audit window: show any model event that became effective
        # since the most recent Friday and up to the selected date. This also
        # captures the backtest's first-day-of-month regime/selection changes.
        return self.events[
            (self.events["effective_date"].dt.date >= analysis_date)
            & (self.events["effective_date"].dt.date <= data_date)
        ]

    def _opportunity_at(self, d: date) -> OpportunityState:
        active = self.opportunities[
            (self.opportunities["entry_date"].dt.date <= d)
            & (self.opportunities["exit_date"].dt.date > d)
        ]
        if active.empty:
            return OpportunityState(active=False)

        r = active.iloc[-1]
        components = {}
        labels = {
            "rs_accel": "RS aceleração",
            "bb_quality": "BBWidth",
            "macd_slope": "MACD slope",
            "extension_atr": "Extensão ATR",
            "higher_low_atr": "Higher low / ATR",
        }
        for col, label in labels.items():
            value = r.get(col)
            if pd.notna(value):
                components[label] = float(value)

        return OpportunityState(
            active=True,
            asset=str(r["asset"]),
            score=float(r["score"]),
            entry_date=r["entry_date"].date(),
            exit_date=r["exit_date"].date(),
            realized_return=float(r["realized_return"]),
            status="Posição hipotética ativa — NÃO GERA ORDEM",
            components=components,
        )

    def _nav_at(self, d: date) -> tuple[float | None, float | None]:
        df = self.performance[self.performance["date"].dt.date <= d]
        if df.empty:
            return None, None
        r = df.iloc[-1]
        return float(r["core_nav"]), float(r["shadow_nav"])

    def result_for(self, selected_date: date) -> WeeklyResult:
        if selected_date < self.min_date:
            data_date = self.min_date
        elif selected_date > self.max_date:
            data_date = self.max_date
        else:
            data_date = selected_date

        analysis_date = _latest_friday(data_date)
        execution_date = analysis_date + timedelta(days=1)
        next_analysis = analysis_date + timedelta(days=7)
        next_execution = next_analysis + timedelta(days=1)

        target_state = self._state_at(data_date)
        target_weights = weights_from_state(str(target_state["regime"]), str(target_state["altcoins"]))

        weekly_events = self._events_in_review_window(analysis_date, data_date)
        if weekly_events.empty:
            current_weights = target_weights.copy()
            had_change = False
        else:
            first_event = weekly_events.iloc[0]
            previous = self._previous_state(first_event)
            current_weights = weights_from_state(str(previous["regime"]), str(previous["altcoins"]))
            had_change = current_weights != target_weights

        all_assets = list(dict.fromkeys(
            ["BTC", "ETH"]
            + [a for a in target_weights if a not in {"BTC", "ETH", "USDT"}]
            + [a for a in current_weights if a not in {"BTC", "ETH", "USDT"}]
            + ["USDT"]
        ))

        positions = []
        for asset in all_assets:
            cw = float(current_weights.get(asset, 0.0))
            tw = float(target_weights.get(asset, 0.0))
            action = classify_action(cw, tw)
            if action == "Manter":
                reason = "Sem alteração nesta revisão"
            elif target_state["event_type"] == "semanal confirmado 2x":
                reason = "Seleção semanal confirmada em 2 revisões"
            elif str(target_state["regime"]) != str(self._previous_state(target_state)["regime"]):
                reason = f"Mudança de regime para {str(target_state['regime']).replace('_',' ').title()}"
            else:
                reason = "Rebalanceamento V4 Core"
            positions.append(PositionTarget(asset, cw, tw, action, reason))

        changes = []
        if had_change:
            changed = [p for p in positions if p.action != "Manter"]
            if changed:
                changes.append(
                    f"{len(changed)} alteração(ões) de peso na V4 Core nesta revisão."
                )
            if target_state["event_type"] == "semanal confirmado 2x":
                alts = _parse_assets(str(target_state["altcoins"]))
                changes.append(
                    "Seleção semanal confirmada em 2 revisões: "
                    + (", ".join(alts) if alts else "sem altcoins.")
                )
            changes.append(
                "Regime vigente: " + str(target_state["regime"]).replace("_"," ").title() + "."
            )
        else:
            changes.append("Nenhuma alteração operacional na V4 Core nesta revisão.")
            changes.append(
                "Regime vigente: " + str(target_state["regime"]).replace("_"," ").title() + "."
            )

        opp = self._opportunity_at(data_date)
        if opp.active:
            changes.append(
                f"V4-O monitora {opp.asset}; posição hipotética, sem geração de ordem."
            )
        else:
            changes.append("V4-O sem oportunidade ativa na data.")

        core_nav, shadow_nav = self._nav_at(data_date)

        latest_event = {
            "effective_date": target_state["effective_date"].date(),
            "event_type": str(target_state["event_type"]),
            "regime": str(target_state["regime"]),
            "altcoins": _parse_assets(str(target_state["altcoins"])),
            "turnover": float(target_state["turnover"]),
            "cost": float(target_state["cost"]),
            "monthly_signal_date": None if pd.isna(target_state["monthly_signal_date"]) else target_state["monthly_signal_date"].date(),
            "monthly_original_selection": _parse_assets(str(target_state["monthly_original_selection"])),
        }

        return WeeklyResult(
            selected_date=selected_date,
            data_date=data_date,
            analysis_date=analysis_date,
            execution_date=execution_date,
            next_analysis_date=next_analysis,
            next_execution_date=next_execution,
            regime=str(target_state["regime"]),
            positions=positions,
            changes=changes,
            opportunity=opp,
            latest_event=latest_event,
            core_nav=core_nav,
            shadow_nav=shadow_nav,
        )

    def performance_until(self, d: date) -> pd.DataFrame:
        d = min(max(d, self.min_date), self.max_date)
        return self.performance[self.performance["date"].dt.date <= d].copy()

    def event_history_until(self, d: date) -> pd.DataFrame:
        d = min(max(d, self.min_date), self.max_date)
        return self.events[self.events["effective_date"].dt.date <= d].copy()

    def opportunities_until(self, d: date) -> pd.DataFrame:
        d = min(max(d, self.min_date), self.max_date)
        return self.opportunities[self.opportunities["entry_date"].dt.date <= d].copy()
