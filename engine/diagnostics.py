"""Read-only diagnostic histories, evaluated as of each observation."""
from datetime import timedelta
import pandas as pd
from engine.live import latest_friday, _weights


def monthly_history(engine, day, months=12):
    rows = []
    for month in pd.period_range(end=pd.Period(day, freq="M"), periods=months, freq="M"):
        observation = month.start_time.date()
        try:
            rows.append({"month": observation, **engine.macro_regime(observation)})
        except RuntimeError:
            continue
    return pd.DataFrame(rows)


def weekly_history(engine, day, weeks=4):
    rows = []
    for offset in reversed(range(weeks)):
        observation = latest_friday(day) - timedelta(weeks=offset)
        try:
            selection = engine.confirmed_selection(observation)
            weights = _weights(selection.regime, selection.confirmed)
            rows.append({"Revisão": observation, "Regime": selection.regime,
                         "Lista recomendada": ", ".join(weights)})
        except RuntimeError:
            rows.append({"Revisão": observation, "Regime": "indisponível", "Lista recomendada": "Histórico insuficiente"})
    return pd.DataFrame(rows)
