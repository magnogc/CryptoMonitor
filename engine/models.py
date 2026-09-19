from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

Regime = Literal["risk_on", "neutral", "defensive"]

@dataclass
class PositionTarget:
    asset: str
    current_weight: float
    target_weight: float
    action: str
    reason: str

@dataclass
class OpportunityState:
    active: bool
    asset: str | None = None
    score: float | None = None
    entry_date: date | None = None
    exit_date: date | None = None
    realized_return: float | None = None
    status: str = "Sem oportunidade ativa"
    components: dict[str, float] = field(default_factory=dict)

@dataclass
class WeeklyResult:
    selected_date: date
    data_date: date
    analysis_date: date
    execution_date: date
    next_analysis_date: date
    next_execution_date: date
    regime: Regime
    positions: list[PositionTarget]
    changes: list[str]
    opportunity: OpportunityState
    latest_event: dict
    core_nav: float | None = None
    shadow_nav: float | None = None
