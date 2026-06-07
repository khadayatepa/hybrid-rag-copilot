"""Persist copilot decisions to Oracle (via MCP). Table names prefixed `copilot_`."""
from __future__ import annotations

from mcp_oracle import OracleMCP


def _create_if_absent(ddl: str) -> str:
    body = ddl.replace("'", "''")
    return (
        "BEGIN EXECUTE IMMEDIATE '" + body + "'; "
        "EXCEPTION WHEN OTHERS THEN IF SQLCODE != -955 THEN RAISE; END IF; END;"
    )


DDL = [
    _create_if_absent(
        "CREATE TABLE copilot_runs (run_id NUMBER PRIMARY KEY, request_id NUMBER, "
        "created_at TIMESTAMP DEFAULT SYSTIMESTAMP, model VARCHAR2(60), "
        "decision VARCHAR2(30), evidence CLOB, rationale CLOB)"
    ),
    "CREATE OR REPLACE VIEW v_copilot_feed AS "
    "SELECT c.run_id, c.request_id, r.reason_text, c.created_at, c.model, c.decision, "
    "c.evidence, c.rationale FROM copilot_runs c "
    "LEFT JOIN refund_requests r ON r.request_id = c.request_id",
]


def _q(text: str) -> str:
    t = (text or "").replace("'", "''")
    t = t.replace("&", "'||CHR(38)||'")
    t = t.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "'||CHR(10)||'")
    return "'" + t + "'"


def _clob_expr(text: str, size: int = 1500) -> str:
    raw = text or ""
    chunks = [raw[i : i + size] for i in range(0, len(raw), size)] or [""]
    return "||".join("TO_CLOB(" + _q(c) + ")" for c in chunks)


async def _exec(mcp: OracleMCP, sql: str, what: str) -> None:
    out = await mcp.run_sql(sql)
    if "ORA-" in out or "Error" in out or "cancelled" in out:
        ora = next((ln for ln in out.splitlines() if "ORA-" in ln), out[:300])
        raise RuntimeError(f"persist {what} FAILED: {ora}")


async def ensure_tables(mcp: OracleMCP) -> None:
    for stmt in DDL:
        await mcp.run_sql(stmt)


async def save_decision(mcp: OracleMCP, *, run_id: int, request_id: int, model: str,
                        decision: str, evidence: str, rationale: str) -> None:
    await _exec(
        mcp,
        "INSERT INTO copilot_runs (run_id, request_id, model, decision, evidence, rationale) VALUES "
        f"({run_id}, {request_id}, {_q(model)}, {_q(decision)}, {_clob_expr(evidence)}, {_clob_expr(rationale)})",
        "insert run",
    )
    await _exec(mcp, "COMMIT", "commit")
