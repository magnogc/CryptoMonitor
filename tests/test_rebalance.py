from engine.rebalance import classify_action


def test_actions():
    assert classify_action(0.0, 0.2) == "Comprar"
    assert classify_action(0.2, 0.0) == "Vender"
    assert classify_action(0.1, 0.2) == "Aumentar"
    assert classify_action(0.2, 0.1) == "Reduzir"
    assert classify_action(0.2, 0.201) == "Manter"
