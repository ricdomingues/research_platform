"""Probes the resolved Streamlit and Plotly APIs the dashboard relies on (D40, D58). No network, no server."""

import plotly.graph_objects as go
from plotly.subplots import make_subplots
from streamlit import config
from streamlit.testing.v1 import AppTest

PROBE = """
import streamlit as st

st.title("probe")
choice = st.sidebar.radio("page", ["a", "b"], key="page")
st.markdown(f"page={choice}")
st.markdown(f"injected={st.session_state.get('injected')}")
if st.button("go", key="go"):
    st.error("clicked")
"""


def test_app_test_runs_scripts_clicks_buttons_and_switches_the_sidebar_radio():
    at = AppTest.from_string(PROBE, default_timeout=30)
    at.session_state["injected"] = "hello"
    at.run()
    assert not at.exception
    assert at.title[0].value == "probe"
    assert [m.value for m in at.markdown] == ["page=a", "injected=hello"]
    at.button(key="go").click().run()
    assert at.error[0].value == "clicked"
    at.radio(key="page").set_value("b").run()
    assert at.markdown[0].value == "page=b"


def test_plotly_subplots_keep_trace_axes_and_named_shapes():
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True)
    figure.add_trace(go.Candlestick(x=["2025-11-25 09:30"], open=[1.0], high=[2.0], low=[0.5], close=[1.5]),
                     row=1, col=1)
    figure.add_trace(go.Bar(x=["2025-11-25 09:30"], y=[10.0]), row=2, col=1)
    figure.add_shape(type="line", xref="x domain", x0=0, x1=1, yref="y", y0=1.2, y1=1.2, name="Stop")
    assert [trace.type for trace in figure.data] == ["candlestick", "bar"]
    assert figure.data[1].yaxis == "y2"
    assert [shape.name for shape in figure.layout.shapes] == ["Stop"]


def test_error_details_can_be_hidden_with_the_none_value():
    # D58: recent Streamlit takes full | stacktrace | type | none; true/false are legacy aliases only.
    option = config.get_config_options()["client.showErrorDetails"]
    assert option.type is str, f"client.showErrorDetails is {option.type}: use the probed value and record a Ruling"
    assert "none" in (option.description or "")
    previous = config.get_option("client.showErrorDetails")
    config.set_option("client.showErrorDetails", "none")
    try:
        assert config.get_option("client.showErrorDetails") == "none"
    finally:
        config.set_option("client.showErrorDetails", previous)
