from engine.core import weights_from_state

def test_risk_on_equal_weights():
    w = weights_from_state("risk_on", "SOL, LINK, BNB")
    assert set(w) == {"BTC","ETH","SOL","LINK","BNB"}
    assert all(abs(x - 0.2) < 1e-12 for x in w.values())

def test_neutral():
    assert weights_from_state("neutral", "") == {"BTC":0.1,"ETH":0.1,"USDT":0.8}

def test_defensive():
    assert weights_from_state("defensive", "SOL") == {"USDT":1.0}
