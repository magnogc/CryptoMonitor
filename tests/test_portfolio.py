from datetime import date, timedelta
import pandas as pd
import pytest
from engine.portfolio import create_portfolio, rebalance, performance, quote


@pytest.fixture
def market():
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(3)]
    return {a + "USDT": pd.DataFrame({"date": days, "close": prices}) for a, prices in
            [("BTC", [100, 110, 121]), ("ETH", [50, 50, 60])]}


def test_identity_reference_and_prospective_return(market):
    p, event = create_portfolio("Teste", {"BTC": 1}, date(2026, 1, 1), market)
    other, _ = create_portfolio("Teste", {"BTC": 1}, date(2026, 1, 1), market)
    assert p["id"] != other["id"]
    assert p["positions"][0]["entry_price"] == 100
    curve = performance(p, [event], market, date(2026, 1, 3))
    assert curve.iloc[0].tolist() == [1, 1, 1]
    assert curve.iloc[-1]["Carteira V4 real"] == pytest.approx(1.21)


def test_rebalance_cash_flow_and_history(market):
    p, e1 = create_portfolio("Teste", {"BTC": 1}, date(2026, 1, 1), market)
    updated, e2 = rebalance(p, {"BTC": 2}, date(2026, 1, 2), market, 110)
    assert p["positions"][0]["quantity"] == 1
    assert e2["before"] == p["positions"]
    assert updated["revision"] == 2
    curve = performance(updated, [e1, e2], market, date(2026, 1, 3))
    assert curve.iloc[-1]["Carteira V4 real"] == pytest.approx(1.21)
    with pytest.raises(ValueError, match="valeurs|valores"):
        rebalance(p, {"BTC": 2}, date(2026, 1, 2), market)


@pytest.mark.parametrize("quantity", [-1, float("nan"), float("inf")])
def test_invalid_quantities(market, quantity):
    with pytest.raises(ValueError):
        create_portfolio("Teste", {"BTC": quantity}, date(2026, 1, 1), market)


def test_missing_stale_and_future_quotes(market):
    for day in [date(2025, 12, 31), date(2026, 2, 1)]:
        with pytest.raises(ValueError):
            quote(market, "BTC", day)
    assert quote(market, "BTC", date(2026, 1, 2))[0] == 110
