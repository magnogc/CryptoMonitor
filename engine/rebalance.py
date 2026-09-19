from __future__ import annotations

def classify_action(current_weight: float, target_weight: float, tol: float = 0.0025) -> str:
    delta = target_weight - current_weight
    if abs(delta) <= tol:
        return "Manter"
    if current_weight <= tol and target_weight > tol:
        return "Comprar"
    if current_weight > tol and target_weight <= tol:
        return "Vender"
    return "Aumentar" if delta > 0 else "Reduzir"
