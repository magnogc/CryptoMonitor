"""Prospective accounting using cached closes, never exchange lifetime PNL."""
from copy import deepcopy
from datetime import date, datetime, timezone
from math import isfinite
from uuid import uuid4

import pandas as pd


def quote(series, asset, day):
    if asset == "USDT":
        return 1.0, day.isoformat()
    data = series.get(asset + "USDT", pd.DataFrame(columns=["date", "close"]))
    rows = data[data.date <= day].sort_values("date")
    if rows.empty:
        raise ValueError(f"Sem cotação de {asset} até {day}.")
    row = rows.iloc[-1]
    price = float(row.close)
    if not isfinite(price) or price <= 0:
        raise ValueError(f"Cotação inválida para {asset}.")
    if (day - row.date).days > 3:
        raise ValueError(f"Cotação de {asset} desatualizada para {day}.")
    return price, row.date.isoformat()


def positions(series, quantities, day):
    result = []
    for asset, quantity in quantities.items():
        quantity = float(quantity)
        if not isfinite(quantity) or quantity < 0:
            raise ValueError("Quantidades devem ser finitas e não negativas.")
        if not quantity:
            continue
        price, price_date = quote(series, asset, day)
        result.append(dict(asset=asset, quantity=quantity, entry_date=day.isoformat(),
                           entry_price=price, price_date=price_date, initial_value=quantity * price))
    if not result:
        raise ValueError("Informe pelo menos uma posição positiva.")
    return result


def create_portfolio(name, quantities, day, series):
    if not name.strip() or len(name.strip()) > 100:
        raise ValueError("Informe um nome de até 100 caracteres.")
    if day > date.today():
        raise ValueError("A entrada não pode estar no futuro.")
    pos = positions(series, quantities, day)
    p = dict(id=uuid4().hex, name=name.strip(), created_at=datetime.now(timezone.utc).isoformat(),
             entry_date=day.isoformat(), model="V4 Core", revision=1, positions=pos,
             initial_value=sum(x["initial_value"] for x in pos),
             benchmark_prices={a: quote(series, a, day)[0] for a in ("BTC", "ETH")})
    return p, dict(type="creation", date=day.isoformat(), revision=1, positions=deepcopy(pos), external_flow=0.0)


def rebalance(p, quantities, day, series, external_flow=0.0):
    if day < date.fromisoformat(p.get("last_rebalance_date", p["entry_date"])) or day > date.today():
        raise ValueError("Data de rebalanceamento fora da sequência da carteira.")
    if not isfinite(external_flow):
        raise ValueError("Aporte/saque inválido.")
    old_value = sum(x["quantity"] * quote(series, x["asset"], day)[0] for x in p["positions"])
    pos = positions(series, quantities, day)
    new_value = sum(x["initial_value"] for x in pos)
    if abs(new_value - old_value - external_flow) > max(0.01, old_value * 1e-6):
        raise ValueError("Os valores devem fechar: posições anteriores + aporte/saque = novas posições. Inclua o saldo USDT.")
    updated = deepcopy(p)
    updated.update(positions=pos, revision=p["revision"]+1, last_rebalance_date=day.isoformat())
    event = dict(type="rebalance", date=day.isoformat(), revision=updated["revision"],
                 before=deepcopy(p["positions"]), positions=deepcopy(pos), external_flow=external_flow)
    return updated, event


def performance(p, history, series, end):
    events = sorted(history, key=lambda x: (x["date"], x["revision"]))
    start = date.fromisoformat(p["entry_date"])
    units = p["initial_value"]
    held = events[0]["positions"]
    rows = []
    cursor = 1
    for stamp in pd.date_range(start, end):
        day = stamp.date()
        while cursor < len(events) and date.fromisoformat(events[cursor]["date"]) <= day:
            event = events[cursor]
            event_day = date.fromisoformat(event["date"])
            before_value = sum(x["quantity"] * quote(series, x["asset"], event_day)[0] for x in held)
            units += event["external_flow"] / (before_value / units)
            held = event["positions"]
            cursor += 1
        value = sum(x["quantity"] * quote(series, x["asset"], day)[0] for x in held)
        rows.append({"Data": day, "Carteira V4 real": value / units,
                     **{a: quote(series, a, day)[0] / p["benchmark_prices"][a] for a in ("BTC", "ETH")}})
    return pd.DataFrame(rows).set_index("Data") if rows else pd.DataFrame()
