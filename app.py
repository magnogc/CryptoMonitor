from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui_v04 import navigate, render_diagnostics, render_portfolio
from config import CONFIG
from data.live_data import cache_status, update_crypto_only, update_sp500_for_config
from engine.core import HistoricalV4Engine
from engine.live import LiveV4Engine, latest_friday


st.set_page_config(page_title=CONFIG.app_title, page_icon="📈", layout="wide")
st.markdown("""
<style>
.block-container {padding-top: 1.15rem; padding-bottom: 2rem; max-width: 1450px;}
[data-testid="stMetric"] {border:1px solid #E5E7EB;border-radius:12px;padding:12px 14px;background:#FFFFFF;}
.status-chip {display:inline-block;padding:7px 12px;border-radius:999px;font-weight:800;font-size:.84rem;}
.risk-on {background:#DCFCE7;color:#166534}.neutral {background:#FEF3C7;color:#92400E}.defensive {background:#FEE2E2;color:#991B1B}
.shadow-warning {border:2px solid #8B5CF6;background:#F5F3FF;border-radius:12px;padding:12px 14px;font-weight:700;color:#5B21B6;}
.live-warning {border:1px solid #0EA5E9;background:#F0F9FF;border-radius:12px;padding:10px 13px;color:#075985;}
</style>
""", unsafe_allow_html=True)

REGIME_LABEL={"risk_on":"RISK ON","neutral":"NEUTRAL","defensive":"DEFENSIVE"}
REGIME_CLASS={"risk_on":"risk-on","neutral":"neutral","defensive":"defensive"}


@st.cache_resource
def historical_engine():
    return HistoricalV4Engine(CONFIG.data_dir)


def live_engine():
    # Não usa cache_resource porque os arquivos podem ser atualizados pelo botão.
    return LiveV4Engine(CONFIG)


def render_positions(result):
    rows=[]
    for p in result.positions:
        delta=p.target_weight-p.current_weight
        rows.append({"Ativo":p.asset,"Peso atual":f"{p.current_weight:.1%}","Peso-alvo":f"{p.target_weight:.1%}",
                     "Δ":f"{delta:+.1%}","Ação":p.action,"Motivo":p.reason,"_delta":delta})
    df=pd.DataFrame(rows)
    if df.empty:
        st.info("Sem posições para exibir.")
        return
    raw=df["_delta"].tolist(); display=df.drop(columns=["_delta"])
    def action_style(v):
        if v in {"Comprar","Aumentar"}: return "color:#166534;font-weight:700"
        if v in {"Vender","Reduzir"}: return "color:#991B1B;font-weight:700"
        return "color:#374151"
    styled=display.style.map(action_style,subset=["Ação"])
    def delta_style(col):
        if col.name!="Δ": return [""]*len(col)
        return [f"color:{'#166534' if x>0 else '#991B1B' if x<0 else '#374151'};font-weight:700" for x in raw]
    styled=styled.apply(delta_style,axis=0)
    st.dataframe(styled,hide_index=True,use_container_width=True)


def render_dashboard(result, live=False):
    title_col,regime_col=st.columns([4,1])
    with title_col:
        st.title("Painel semanal")
        prefix="AO VIVO" if live else "REPLAY HISTÓRICO"
        st.caption(f"{prefix} · dados até {result.data_date.strftime('%d/%m/%Y')} · análise: sexta {result.analysis_date.strftime('%d/%m/%Y')} · execução: sábado {result.execution_date.strftime('%d/%m/%Y')}")
    with regime_col:
        st.markdown(f'<span class="status-chip {REGIME_CLASS[result.regime]}">{REGIME_LABEL[result.regime]}</span>',unsafe_allow_html=True)

    if live:
        st.button("Regime → Diagnóstico Macro", on_click=navigate, args=("Macro",))

    changes=sum(p.action!="Manter" for p in result.positions)
    m1,m2,m3,m4=st.columns(4)
    m1.metric("Regime",REGIME_LABEL[result.regime]);m2.metric("Alterações na Core",changes)
    m3.metric("Oportunidade V4-O",result.opportunity.asset if result.opportunity.active else "Nenhuma")
    m4.metric("Turnover estimado",f"{result.latest_event.get('turnover',0):.1%}")
    n1,n2=st.columns(2)
    n1.caption(f"Próxima análise: **sexta, {result.next_analysis_date.strftime('%d/%m/%Y')}**")
    n2.caption(f"Execução prevista: **sábado, {result.next_execution_date.strftime('%d/%m/%Y')}**")

    if live:
        st.markdown('<div class="live-warning"><b>V4 Core ao vivo:</b> cálculo com dados públicos cacheados. O app apenas sugere pesos; não envia ordens para corretora.</div>',unsafe_allow_html=True)

    st.subheader("Carteira V4 Core")
    if live:
        st.button("Carteira V4 Core → Sinais V4", on_click=navigate, args=("Sinais V4",))
    render_positions(result)
    left,right=st.columns([1.15,1])
    with left:
        st.subheader("O que mudou esta semana?")
        for x in result.changes: st.markdown(f"- {x}")
    with right:
        st.subheader("Ações sugeridas")
        acts=[p for p in result.positions if p.action!="Manter"]
        if not acts: st.success("Nenhuma ordem sugerida nesta revisão.")
        for p in acts: st.markdown(f"**{p.asset}: {p.current_weight:.1%} → {p.target_weight:.1%}** · {p.action}")

    st.divider(); st.subheader("V4-O — monitoramento")
    st.markdown('<div class="shadow-warning">SIMULAÇÃO — NÃO GERA ORDEM</div>',unsafe_allow_html=True);st.write("")
    if result.opportunity.active:
        c1,c2,c3,c4=st.columns(4)
        c1.metric("Ativo",result.opportunity.asset);c2.metric("Score estrutural",f"{result.opportunity.score:.2f}")
        c3.metric("Entrada hipotética",result.opportunity.entry_date.strftime('%d/%m/%Y') if result.opportunity.entry_date else "—")
        c4.metric("Peso experimental",f"{CONFIG.opportunity_weight:.0%}")
        if result.opportunity.components:
            fig=go.Figure(go.Bar(x=list(result.opportunity.components.values()),y=list(result.opportunity.components.keys()),orientation="h"))
            fig.update_layout(height=315,margin=dict(l=0,r=0,t=15,b=0),showlegend=False,xaxis_title="Componente normalizado")
            st.plotly_chart(fig,use_container_width=True)
        if not live and result.opportunity.exit_date:
            st.caption(f"Saída histórica: {result.opportunity.exit_date.strftime('%d/%m/%Y')} · retorno da operação: {result.opportunity.realized_return:+.1%}. Não é previsão.")
        else:
            st.caption("Candidato calculado pela arquitetura congelada da V4-O. Uso apenas prospectivo/observacional.")
    else: st.info("Nenhuma oportunidade V4-O ativa nesta revisão.")


hist=historical_engine()
st.sidebar.title("V4 Crypto Monitor")
engine_mode=st.sidebar.radio("Motor",["Ao vivo (v0.4)","Replay histórico"],index=0)
view=st.sidebar.radio("Tela",["Painel semanal","Carteira real","Histórico","Diagnóstico","Dados"],index=0,key="view")

if engine_mode=="Ao vivo (v0.4)":
    status=cache_status(CONFIG)
    st.sidebar.divider();st.sidebar.caption("V4 Core = operacional · V4-O = monitoramento")

    # Binance state and action
    if status["latest_crypto"]:
        if status["crypto_current"]:
            st.sidebar.success(f"Dados atualizados até {status['latest_crypto'].strftime('%d/%m/%Y')} — nenhuma ação necessária")
        else:
            st.sidebar.warning(f"Binance disponível até {status['latest_crypto'].strftime('%d/%m/%Y')}. Atualização recomendada.")
    else:
        st.sidebar.warning("Base Binance ainda não carregada.")
    crypto_disabled = bool(status["crypto_current"])
    if st.sidebar.button("Atualizar Binance",type="primary",use_container_width=True,disabled=crypto_disabled):
        bar=st.sidebar.progress(0.0,text="Preparando atualização...")
        def progress(i,total,symbol):
            bar.progress(i/max(total,1),text=f"Binance {i}/{total}: {symbol}")
        try:
            stats=update_crypto_only(CONFIG,progress)
            bar.progress(1.0,text="Binance atualizada")
            st.sidebar.success(f"{stats.symbols} símbolos · {stats.rows_downloaded} novas linhas · {stats.elapsed_seconds:.0f}s")
            st.rerun()
        except Exception as exc:
            st.sidebar.error(f"Falha na atualização da Binance: {exc}")

    st.sidebar.write("")
    # S&P 500 state and action; only monthly freshness matters.
    if status["latest_sp500"]:
        if status["sp500_current"]:
            st.sidebar.success(f"Dados atualizados até {status['latest_sp500'].strftime('%d/%m/%Y')} — nenhuma ação necessária")
        else:
            st.sidebar.warning(f"S&P 500 disponível até {status['latest_sp500'].strftime('%d/%m/%Y')}. Atualização mensal recomendada.")
    else:
        st.sidebar.warning("Base S&P 500 ainda não disponível.")
    sp_disabled = bool(status["sp500_current"])
    if st.sidebar.button("Atualizar S&P 500",use_container_width=True,disabled=sp_disabled):
        try:
            sp_date, updated, source = update_sp500_for_config(CONFIG,force=True)
            if updated:
                st.sidebar.success(f"S&P 500 atualizado via {source} até {sp_date.strftime('%d/%m/%Y') if sp_date else '—'}.")
            else:
                st.sidebar.warning(f"Fontes externas indisponíveis. Mantida a cópia local até {sp_date.strftime('%d/%m/%Y') if sp_date else '—'}.")
            st.rerun()
        except Exception as exc:
            st.sidebar.error(f"Falha na atualização do S&P 500: {exc}")

    if not status["ready"]:
        st.title("V4 Crypto Monitor v0.4")
        st.info("Use **Atualizar Binance** na barra lateral para a primeira carga. O S&P 500 já possui um bootstrap local e só precisa de atualização quando um novo sinal mensal exigir dados mais recentes.")
        st.stop()

    live=live_engine(); max_date=live.max_date or date.today()
    selected_date=st.sidebar.date_input("Data de referência",value=max_date,min_value=max(hist.min_date,date(2020,1,1)),max_value=max_date)
    try:
        result=live.result_for(selected_date)
    except Exception as exc:
        st.error(f"Não foi possível calcular a V4 ao vivo: {exc}");st.stop()

    if view=="Painel semanal":
        render_dashboard(result,live=True)
        if st.button("Salvar snapshot desta revisão"):
            live.save_snapshot(result);st.success("Snapshot salvo no histórico prospectivo local.")
    elif view=="Histórico":
        st.title("Histórico prospectivo")
        st.caption("A v0.3 inicia o registro prospectivo. O backtest consolidado continua disponível no modo Replay histórico.")
        if CONFIG.live_history_file.exists():
            rows=[]
            import json
            for line in CONFIG.live_history_file.read_text(encoding="utf-8").splitlines():
                try: rows.append(json.loads(line))
                except Exception: pass
            if rows:
                tab=pd.DataFrame([{ "Data":x.get("analysis_date"),"Regime":x.get("regime"),"Seleção":", ".join(x.get("latest_event",{}).get("altcoins",[])),"Oportunidade":x.get("opportunity",{}).get("asset") or "—"} for x in rows])
                st.dataframe(tab.iloc[::-1],hide_index=True,use_container_width=True)
            else: st.info("Ainda não há snapshots salvos.")
        else: st.info("Ainda não há snapshots salvos. No Painel semanal, use **Salvar snapshot desta revisão**.")
    elif view=="Diagnóstico":
        render_diagnostics(live, result)
    elif view=="Carteira real":
        render_portfolio(live, result)
    else:
        st.title("Dados e conectividade")
        st.write("**Binance:** mercado Spot público, candles diários e volume cotado em USDT.")
        st.write("**S&P 500:** cópia local com bootstrap; atualização mensal via FRED e fallback Stooq somente quando necessária.")
        st.write("**Cache local:**",str(CONFIG.cache_dir))
        st.json({k:(v.isoformat() if hasattr(v,'isoformat') else v) for k,v in cache_status(CONFIG).items()})
        st.caption("Dados de mercado não exigem chave. A persistência privada usa token configurado nos Secrets. O app não acessa saldo, conta nem envia ordens.")

else:
    st.sidebar.divider();st.sidebar.success(f"Replay real: {hist.min_date.strftime('%d/%m/%Y')} a {hist.max_date.strftime('%d/%m/%Y')}")
    selected_date=st.sidebar.date_input("Data de referência",value=hist.max_date,min_value=hist.min_date,max_value=hist.max_date)
    result=hist.result_for(selected_date)
    if view=="Painel semanal": render_dashboard(result,live=False)
    elif view=="Histórico":
        st.title("Histórico real")
        perf=hist.performance_until(selected_date).copy();perf["Data"]=perf["date"]
        fig=go.Figure();fig.add_trace(go.Scatter(x=perf.Data,y=perf.core_nav,name="V4 Core"));fig.add_trace(go.Scatter(x=perf.Data,y=perf.shadow_nav,name="V4-O"))
        fig.update_layout(height=430,yaxis_title="Capital acumulado (base 1,0)",margin=dict(l=0,r=0,t=30,b=0),legend=dict(orientation="h"));st.plotly_chart(fig,use_container_width=True)
        core=perf.iloc[-1].core_nav;shadow=perf.iloc[-1].shadow_nav
        c1,c2,c3=st.columns(3);c1.metric("V4 Core",f"{core:.2f}x");c2.metric("V4-O",f"{shadow:.2f}x");c3.metric("Diferença V4-O",f"{shadow/core-1:+.1%}")
    elif view=="Carteira real":
        st.info("Abra o motor Ao vivo para gerenciar a carteira real.")
    elif view=="Diagnóstico":
        st.title("Diagnóstico histórico");st.json({k:(v.isoformat() if hasattr(v,'isoformat') else v) for k,v in result.latest_event.items()})
    else:
        st.title("Dados");st.info("No Replay histórico, os eventos e curvas são os artefatos congelados do backtest. Troque para **Ao vivo (v0.4)** para atualizar Binance/FRED.")
