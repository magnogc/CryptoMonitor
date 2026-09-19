from datetime import date

from engine.live import _weights, latest_friday, previous_month_end


def test_weights():
    assert _weights("defensive", ["SOL"]) == {"USDT": 1.0}
    assert _weights("neutral", []) == {"BTC": 0.10, "ETH": 0.10, "USDT": 0.80}
    w = _weights("risk_on", ["SOL", "LINK", "BNB"])
    assert set(w) == {"BTC", "ETH", "SOL", "LINK", "BNB"}
    assert all(abs(v - 0.2) < 1e-12 for v in w.values())


def test_calendar_helpers():
    assert latest_friday(date(2026, 9, 18)) == date(2026, 9, 18)
    assert latest_friday(date(2026, 9, 20)) == date(2026, 9, 18)
    assert previous_month_end(date(2026, 9, 18)) == date(2026, 8, 31)
