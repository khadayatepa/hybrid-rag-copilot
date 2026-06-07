"""
Streamlit dashboard for the refund-compliance copilot (reads Oracle via SQLcl MCP).
Run:  streamlit run src/dashboard.py
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import os
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st

import config
import dashboard_data as dd
from mcp_oracle import open_oracle_mcp

DECISION_COLOR = {"APPROVE": "#16a34a", "DENY": "#dc2626", "ESCALATE": "#d97706", "UNKNOWN": "#6366f1"}


def _load_all_sync() -> list[dict]:
    async def _go() -> list[dict]:
        async with open_oracle_mcp(config.SQLCL_COMMAND, config.ORACLE_MCP_CONNECTION) as mcp:
            runs = await dd.list_runs(mcp)
            out = []
            for r in runs:
                d = await dd.get_run(mcp, r["run_id"])
                if d:
                    out.append(d)
            return out
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(lambda: asyncio.run(_go())).result()


@st.cache_data(show_spinner="Loading copilot decisions from Oracle 26ai via SQLcl MCP…")
def load_all(nonce: int) -> list[dict]:
    return _load_all_sync()


def main() -> None:
    st.set_page_config(page_title="Refund Copilot · Oracle 26ai", page_icon="🤝", layout="wide")
    st.title("🤝 Refund-Compliance Copilot")
    st.caption("Hybrid RAG on Oracle 26ai — one query joins policy vectors with order facts · via the SQLcl MCP server")

    with st.sidebar:
        st.header("Controls")
        nonce = st.session_state.setdefault("nonce", 0)
        if st.button("🔄 Refresh from database"):
            st.session_state["nonce"] = nonce + 1
            st.cache_data.clear()
            st.rerun()

    runs = load_all(st.session_state["nonce"])
    if not runs:
        st.warning("No decisions yet. Run `python src/copilot.py` first.")
        return

    with st.sidebar:
        opts = {f"#{r['run_id']} · request {r['request_id']} · {r.get('decision')}": r for r in runs}
        run = opts[st.selectbox("Decision", list(opts.keys()))]

    c = DECISION_COLOR.get(run.get("decision", ""), "#6366f1")
    st.markdown(f"### Request {run['request_id']} &nbsp; "
                f"<span style='background:{c};color:#fff;padding:3px 14px;border-radius:16px;font-size:0.6em;'>"
                f"{run.get('decision')}</span>", unsafe_allow_html=True)
    if run.get("reason_text"):
        st.markdown(f"> *“{run['reason_text']}”*")

    st.subheader("Decision rationale")
    st.markdown(run.get("rationale") or "—")

    with st.expander("Hybrid evidence (vector policy search + order facts, one SQL)"):
        st.code(run.get("evidence") or "—")


if __name__ == "__main__":
    main()
else:
    main()
