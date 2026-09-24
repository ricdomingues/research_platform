"""The manual-review panel (Plan 7, D104-D105): a self-refreshing strip of states above the chart.

The page decides nothing. It asks `GET /signals/actionability`, renders the state the engine answered, and
forgets it: a refresh that fails shows an error rather than the last green card, because a panel that pulses
has to fail towards saying nothing. Only a state this module knows pulses; anything else renders muted.
"""

from __future__ import annotations

from html import escape

import streamlit as st

from dashboard.client import ApiClient
from dashboard.viewmodels import (
    PANEL_EMPTY,
    STALE_NOTICE,
    PanelRow,
    fmt_ts,
    panel_rows,
    watch_rows,
)
from dashboard.views.common import guarded

REFRESH = "5s"
WATCH_LIMIT = 20

_CSS = """
<style>
.vo-panel { display: flex; flex-wrap: wrap; gap: .5rem; margin: .25rem 0 .75rem 0; }
.vo-card { border: 1px solid; border-radius: .5rem; padding: .55rem .8rem; min-width: 190px; }
.vo-state { font-weight: 700; font-size: .95rem; letter-spacing: .04em; }
.vo-ticker { font-weight: 700; font-size: 1.05rem; }
.vo-reason { font-size: .8rem; opacity: .85; }
.vo-levels { font-size: .75rem; opacity: .7; margin-top: .25rem; }
.vo-actionable { border-color: #1a7f37; background: #0f2e19; color: #d7f5df; }
.vo-exit { border-color: #b35c00; background: #33210a; color: #ffe7c2; }
.vo-muted { border-color: #4a4a4a; background: #1e1e1e; color: #c9c9c9; }
.vo-stale { border-color: #3a3a3a; background: #191919; color: #8a8a8a; }
.vo-pulse { animation: vo-blink 1.2s ease-in-out infinite; }
@keyframes vo-blink { 0%, 100% { opacity: 1; } 50% { opacity: .45; } }
@media (prefers-reduced-motion: reduce) { .vo-pulse { animation: none; } }
</style>
"""


def _card(row: PanelRow) -> str:
    classes = f"vo-card vo-{row.tone}" + (" vo-pulse" if row.pulse else "")
    return (
        f'<div class="{classes}">'
        f'<div class="vo-ticker">{escape(row.ticker)} <span class="vo-state">{escape(row.state_label)}</span></div>'
        f'<div class="vo-reason">{escape(row.reason)}</div>'
        f'<div class="vo-levels">zona {escape(row.zone)} · stop {escape(row.stop)} · '
        f'alvo {escape(row.targets)} · até {escape(row.valid_until)}</div>'
        f"</div>"
    )


@st.fragment(run_every=REFRESH)
def render(client: ApiClient) -> None:
    """Re-runs on its own every few seconds without touching the rest of the page."""
    st.markdown(_CSS, unsafe_allow_html=True)
    payload = guarded(client.signals_actionability)
    if payload is None:
        return  # guarded already said so; nothing stale is left on screen
    rows = panel_rows(payload)
    if not rows:
        st.info(PANEL_EMPTY)
    else:
        st.markdown(f'<div class="vo-panel">{"".join(_card(row) for row in rows)}</div>', unsafe_allow_html=True)
    # The freshness of the answer is part of the answer, so it is never optional (D104).
    st.caption(f"Estado as-of {fmt_ts(payload.get('as_of'))} · dados as-of {fmt_ts(payload.get('data_as_of'))}. "
               f"{STALE_NOTICE}")


def render_watch(client: ApiClient) -> None:
    """WATCH lives beside the panel: candidates the promotion policy still blocks, in the policy's own codes."""
    envelope = guarded(lambda: client.research_candidates(limit=WATCH_LIMIT)) or {}
    blocked = watch_rows(envelope.get("candidates", []))
    if not blocked:
        return
    with st.expander(f"WATCH — {len(blocked)} candidato(s) ainda bloqueado(s) pela política"):
        st.dataframe(blocked, hide_index=True)
        if envelope.get("score_interpretation"):
            st.caption(str(envelope["score_interpretation"]))
