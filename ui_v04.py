"""Streamlit views for diagnostics and privately persisted live portfolios."""
from datetime import date
import os
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine.diagnostics import monthly_history, weekly_history
from engine.portfolio import create_portfolio, rebalance, performance
from storage.portfolios import GitHubPortfolioStore, StorageError


def navigate(section):
    st.session_state["view"] = "Diagnóstico"
    st.session_state["diagnostic_section"] = section


def render_diagnostics(live, result):
    st.title("Diagnóstico ao vivo")
    section = st.radio("Seção", ["Macro", "Universo estrutural", "Sinais V4"],
                       key="diagnostic_section", horizontal=True)
    diag = live.diagnostics(result.analysis_date)
    if section == "Macro":
        st.write("O regime é fixado mensalmente pela última referência do mês anterior: "
                 "RISK ON quando BTC 30d supera S&P 500 30d; caso contrário, NEUTRAL se BTC 90d > 0; "
                 "nos demais casos, DEFENSIVE. Retornos usam fechamentos disponíveis até a referência. "
                 "Se o S&P estiver atrasado, o motor usa a última referência disponível, indicada abaixo.")
        table = monthly_history(live, result.analysis_date)
        if table.empty:
            st.info("Histórico macro insuficiente.")
            return
        st.dataframe(table.rename(columns={"month": "Mês de vigência", "signal_date": "Referência usada",
            "requested_signal_date": "Referência prevista", "macro_stale": "Referência atrasada", "regime": "Regime"}),
            hide_index=True, use_container_width=True)
        fig = go.Figure()
        for col, name in [("btc_30d", "BTC 30d"), ("sp500_30d", "S&P 500 30d"), ("btc_90d", "BTC 90d")]:
            fig.add_trace(go.Scatter(x=table.month, y=table[col], name=name))
        colors = {"risk_on": "#86efac", "neutral": "#fde68a", "defensive": "#fca5a5"}
        for row in table.itertuples():
            fig.add_vrect(x0=row.month, x1=(pd.Timestamp(row.month)+pd.offsets.MonthBegin(1)).date(),
                          fillcolor=colors[row.regime], opacity=.2, line_width=0, layer="below")
        fig.update_layout(yaxis_tickformat=".0%", height=400)
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Faixas: verde = RISK ON · amarelo = NEUTRAL · vermelho = DEFENSIVE. Até 12 meses, conforme a base disponível.")
    elif section == "Universo estrutural":
        st.dataframe(diag.universe, hide_index=True, use_container_width=True)
    else:
        st.write("**Baseline mensal:**", ", ".join(diag.baseline) or "—")
        st.write("**Candidatas:**", ", ".join(diag.candidates) or "—")
        st.write("**Confirmadas:**", ", ".join(diag.confirmed) or "—", f"· persistência {diag.confirmation_count}x")
        st.caption("A lista ordenada precisa se repetir em duas revisões semanais para substituir a seleção confirmada. "
                   "O regime determina os pesos finais; candidatas não são ordens executadas.")
        with st.expander("Como ler as colunas e os critérios", expanded=True):
            st.markdown("""
| Coluna | Significado |
|---|---|
| asset / symbol | Moeda e par cotado em USDT |
| rs_persist | Fração de 8 semanas em que a moeda superou BTC e ETH |
| hhhl | Fração de 8 semanas com máxima e mínima ascendentes |
| ma_score | Fração de quatro condições: preço > EMA20, EMA20 > EMA50 e ambas ascendentes |
| adx / plus_di / minus_di | Força de tendência e indicadores direcionais de 14 dias |
| adx_vote / macd_vote | ADX normalizado ≥ 0,40 com +DI > −DI / histograma MACD positivo |
| trend_votes | Votos de RS ≥ 0,50, HH/HL ≥ 0,50, médias ≥ 0,75, ADX e MACD (0–5) |
| correction / dd20 | Preço abaixo da EMA20 nos últimos 10 dias ou queda ≥ 8% do máximo de 20 dias / queda atual |
| recovered_ema10 / extension_atr | Preço recuperou EMA10 / distância à EMA20 em ATR14 |
| ao_turn / macd_accel / rsi_turn / stoch_turn | AO e MACD crescentes, RSI acima de 3 dias atrás, cruzamento estocástico com mínimo recente < 30 |
| accel_votes | Soma dos quatro sinais de aceleração |
| continuation | Pelo menos 3 votos de tendência |
| pivot | Correção + recuperação EMA10 + ≥ 2 acelerações + ≥ 1 voto + extensão ≤ 2 ATR |
| setup_confidence | Satisfação normalizada das regras; ordenação, não probabilidade de lucro |
| solidity / current_rank | Solidez estrutural e posição no ranking mensal de volume |
""")
        if not diag.signals.empty:
            st.dataframe(diag.signals, hide_index=True, use_container_width=True)
        st.subheader("Últimas quatro revisões semanais")
        st.caption("Recalculadas com a base disponível em cada data; não representam operações executadas.")
        st.dataframe(weekly_history(live, result.analysis_date), hide_index=True, use_container_width=True)
        assets = list(dict.fromkeys([p.asset for p in result.positions if p.target_weight > 0 or p.current_weight > 0] + diag.candidates))
        assets = [a for a in assets if a != "USDT"]
        st.markdown(" · ".join(f"[{a}](#coin-{a.lower()})" for a in assets))
        start = (pd.Timestamp(result.data_date) - pd.DateOffset(months=6)).date()
        for asset in assets:
            st.markdown(f'<h3 id="coin-{asset.lower()}">{asset}</h3>', unsafe_allow_html=True)
            data = live.series.get(asset + "USDT", pd.DataFrame())
            if data.empty:
                st.info("Sem dados na base.")
                continue
            data = data[(data.date >= start) & (data.date <= result.data_date)]
            fig = go.Figure(go.Scatter(x=data.date, y=data.close, name=asset))
            fig.add_vline(x=result.analysis_date, line_dash="dot")
            fig.update_layout(height=300, yaxis_title="Fechamento (USDT)")
            st.plotly_chart(fig, use_container_width=True, key=f"chart-{asset}")
        st.caption("Linha pontilhada: última revisão semanal. Gráficos limitados aos dados disponíveis.")


def get_store():
    try:
        settings = dict(st.secrets.get("portfolio_storage", {}))
    except FileNotFoundError:
        settings = {}
    token = settings.get("token") or os.environ.get("CRYPTOMONITOR_DATA_TOKEN")
    return GitHubPortfolioStore(token, settings.get("repository", "magnogc/CryptoMonitorData"), settings.get("branch", "main"))


def render_portfolio(live, result):
    st.title("Carteira real / teste ao vivo")
    st.caption("Registro prospectivo em USDT: retorno zerado na entrada, separado do PNL antigo da corretora. "
               "Preços de referência são fechamentos da base, não preços reais de execução. Nenhuma ordem é enviada.")
    st.info("As carteiras são compartilhadas entre as pessoas com acesso a este app. Nome e ID distinguem carteiras; não são autenticação.")
    try:
        store = get_store()
        index = store.list_portfolios()
        selected = st.selectbox("Carteira", [None] + [x["id"] for x in index],
            format_func=lambda v: "Criar nova carteira" if v is None else next(x["name"] for x in index if x["id"] == v) + " · " + v)
        loaded_key = f"loaded-portfolio-{selected}"
        if selected:
            if st.button("Recarregar a carteira"):
                st.session_state.pop(loaded_key, None)
            if loaded_key not in st.session_state:
                st.session_state[loaded_key] = store.load(selected)
            p, history = st.session_state[loaded_key]
        else:
            p, history = None, []
        assets = sorted({s[:-4] for s in live.series} | {"USDT"})
        initial = {x["asset"]: x["quantity"] for x in p["positions"]} if p else {
            x.asset: 0.0 for x in result.positions if x.target_weight > 0}
        form_key = f"portfolio-{selected or 'new'}"
        with st.form(form_key):
            name = st.text_input("Nome da carteira", value=p["name"] if p else "", disabled=bool(p), max_chars=100)
            day = st.date_input("Data do rebalanceamento" if p else "Data de entrada", value=min(date.today(), live.max_date), max_value=min(date.today(), live.max_date))
            st.caption("Informe as quantidades efetivamente mantidas, incluindo saldo USDT. Adicione/remova linhas conforme necessário.")
            edited = st.data_editor(pd.DataFrame([{"Ativo": a, "Quantidade": q} for a, q in initial.items()]),
                num_rows="dynamic", hide_index=True, key=form_key+"-positions", column_config={
                    "Ativo": st.column_config.SelectboxColumn(options=assets, required=True),
                    "Quantidade": st.column_config.NumberColumn(min_value=0.0, required=True, format="%.8f")})
            flow = st.number_input("Aporte (+) ou saque (−), em USDT", value=0.0) if p else 0.0
            submit = st.form_submit_button("Registrar rebalanceamento" if p else "Criar carteira")
        if submit:
            if edited.Ativo.duplicated().any() or edited.isna().any().any():
                raise ValueError("Preencha todas as linhas e use cada ativo uma única vez.")
            quantities = dict(zip(edited.Ativo, edited.Quantidade))
            if any(a not in assets for a in quantities):
                raise ValueError("Ativo não disponível na base.")
            updated, event = rebalance(p, quantities, day, live.series, flow) if p else create_portfolio(name, quantities, day, live.series)
            store.save(updated, event, expected_revision=p["revision"] if p else None)
            st.success(f"Carteira salva: {updated['name']} · ID {updated['id']}")
            p = updated
            history = history + [event]
            if selected:
                st.session_state[loaded_key] = (p, history)
        if p:
            st.write("**ID:**", p["id"], "· **Entrada:**", p["entry_date"])
            st.dataframe(pd.DataFrame(p["positions"]), hide_index=True, use_container_width=True)
            st.subheader("Carteira V4 real × BTC × ETH")
            st.caption("Base 1 na entrada. Retorno da carteira ajustado por cotas para neutralizar aportes/saques. Benchmarks buy-and-hold, sem custos.")
            try:
                curve = performance(p, history, live.series, min(date.today(), live.max_date))
                st.line_chart(curve)
            except ValueError as exc:
                st.warning(str(exc))
            st.subheader("Histórico de rebalanceamentos")
            st.dataframe(pd.DataFrame([{"Data": e["date"], "Evento": e["type"], "Revisão": e["revision"],
                "Aporte/saque": e["external_flow"], "Posições": ", ".join(f"{x['asset']}: {x['quantity']:g}" for x in e["positions"])} for e in history]), hide_index=True)
    except (StorageError, ValueError) as exc:
        st.error(str(exc))
