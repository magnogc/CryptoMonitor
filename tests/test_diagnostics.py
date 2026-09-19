from datetime import date
from types import SimpleNamespace
from engine.diagnostics import monthly_history, weekly_history


def test_four_revisions_are_asof_and_include_regime_weights():
    calls = []
    def selection(day):
        calls.append(day)
        return SimpleNamespace(regime="neutral", confirmed=["SOL"])
    rows = weekly_history(SimpleNamespace(confirmed_selection=selection), date(2026, 9, 19))
    assert calls == [date(2026, 8, 28), date(2026, 9, 4), date(2026, 9, 11), date(2026, 9, 18)]
    assert set(rows["Lista recomendada"]) == {"BTC, ETH, USDT"}


def test_monthly_observations_never_use_future_month():
    calls = []
    def macro(day):
        calls.append(day)
        return {"regime": "risk_on"}
    rows = monthly_history(SimpleNamespace(macro_regime=macro), date(2026, 9, 18))
    assert len(rows) == 12
    assert calls[0] == date(2025, 10, 1)
    assert calls[-1] == date(2026, 9, 1)
