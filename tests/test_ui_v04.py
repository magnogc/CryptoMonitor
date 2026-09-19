from streamlit.testing.v1 import AppTest


def test_diagnostic_sections_and_charts():
    script = '''
from datetime import date
from types import SimpleNamespace
import pandas as pd
from ui_v04 import render_diagnostics
selection = SimpleNamespace(baseline=["SOL"], candidates=["SOL"], confirmed=["SOL"],
    confirmation_count=2, regime="risk_on", signals=pd.DataFrame(), universe=pd.DataFrame())
def macro(day):
    return dict(signal_date=day, requested_signal_date=day, macro_stale=False,
                btc_30d=.1, sp500_30d=.02, btc_90d=.2, regime="risk_on")
live = SimpleNamespace(diagnostics=lambda d: selection, confirmed_selection=lambda d: selection,
    macro_regime=macro, series={"SOLUSDT": pd.DataFrame({"date": [date(2026,9,18)], "close": [100]})})
result = SimpleNamespace(analysis_date=date(2026,9,18), data_date=date(2026,9,19),
    positions=[SimpleNamespace(asset="SOL", target_weight=.2, current_weight=.2)])
render_diagnostics(live, result)
'''
    at = AppTest.from_string(script).run(timeout=30)
    assert not at.exception
    assert len(at.dataframe[0].value) == 12
    at.radio[0].set_value("Sinais V4").run(timeout=30)
    assert not at.exception
    assert len(at.dataframe[0].value) == 4
    assert any("#coin-sol" in m.value for m in at.markdown)


def test_dashboard_links_select_correct_diagnostic():
    script = '''
import streamlit as st
from ui_v04 import navigate
st.radio("Tela", ["Painel semanal", "Diagnóstico"], key="view")
st.button("Regime", on_click=navigate, args=("Macro",))
st.button("Carteira V4 Core", on_click=navigate, args=("Sinais V4",))
if st.session_state.view == "Diagnóstico":
    st.radio("Seção", ["Macro", "Universo estrutural", "Sinais V4"], key="diagnostic_section")
'''
    at = AppTest.from_string(script).run()
    at.button[1].click().run()
    assert not at.exception
    assert at.radio[0].value == "Diagnóstico"
    assert at.radio[1].value == "Sinais V4"
    at.button[0].click().run()
    assert at.radio[1].value == "Macro"
