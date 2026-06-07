"""
Tools for the refund-compliance copilot — all READ-ONLY.

The star is `assess_refund`: it runs ONE Oracle query that combines a VECTOR
similarity search over policy clauses with a relational JOIN to the order facts
(via CROSS JOIN LATERAL). That's the whole point — vectors and tables answered in
a single statement, no separate vector store.
"""
from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

import config
from mcp_oracle import OracleMCP

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "assess_refund",
            "description": (
                "Given a refund request id, return the order facts AND the most relevant "
                "refund-policy clauses in one hybrid query (relational JOIN + vector search). "
                "Use this first — it gives you everything needed to decide."
            ),
            "parameters": {
                "type": "object",
                "properties": {"request_id": {"type": "integer"}},
                "required": ["request_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "policy_search",
            "description": "Semantic search over refund-policy clauses. Returns the closest clauses with distance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query_text": {"type": "string"},
                    "k": {"type": "integer", "default": 3},
                },
                "required": ["query_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": (
                "Run a read-only SELECT for follow-up facts. Tables: "
                "orders(order_id, customer_id, product, amount, order_date, delivered_date, status, channel); "
                "refund_requests(request_id, order_id, reason_text, requested_at); "
                "policies(policy_id, title, category, clause_text)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
            },
        },
    },
]


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def _is_readonly(sql: str) -> bool:
    s = sql.strip()
    while s.startswith("("):
        s = s[1:].strip()
    u = s.upper()
    return u.startswith("SELECT") or u.startswith("WITH")


async def _embed(client: AsyncOpenAI, text: str) -> list[float]:
    resp = await client.embeddings.create(model=config.EMBED_MODEL, input=text)
    return resp.data[0].embedding


def hybrid_sql(request_id: int, vlit: str) -> str:
    """One statement: order facts (relational) + nearest policy clauses (vector)."""
    return (
        "SELECT r.request_id, o.order_id, o.product, o.amount, "
        "TO_CHAR(o.order_date,'YYYY-MM-DD') AS order_date, "
        "TO_CHAR(o.delivered_date,'YYYY-MM-DD') AS delivered_date, o.status, o.channel, "
        "TRUNC(SYSDATE - o.delivered_date) AS days_since_delivery, r.reason_text, "
        "p.title AS policy_title, p.category AS policy_category, "
        f"ROUND(VECTOR_DISTANCE(p.embedding, TO_VECTOR('{vlit}'), COSINE),4) AS policy_distance, "
        "p.clause_text "
        "FROM refund_requests r "
        "JOIN orders o ON o.order_id = r.order_id "
        "CROSS JOIN LATERAL ("
        "  SELECT title, category, clause_text, embedding FROM policies "
        f"  ORDER BY VECTOR_DISTANCE(embedding, TO_VECTOR('{vlit}'), COSINE) FETCH FIRST 2 ROWS ONLY"
        ") p "
        f"WHERE r.request_id = {int(request_id)}"
    )


async def dispatch(name: str, args: dict[str, Any], *, mcp: OracleMCP, openai_client: AsyncOpenAI) -> str:
    if name == "assess_refund":
        rid = int(args["request_id"])
        reason = await mcp.run_sql(f"SELECT reason_text FROM refund_requests WHERE request_id = {rid}")
        # embed the customer's stated reason, then run the hybrid query
        vec = await _embed(openai_client, reason)
        return await mcp.run_sql(hybrid_sql(rid, _vector_literal(vec)))

    if name == "policy_search":
        k = max(1, min(int(args.get("k", 3) or 3), 10))
        vec = await _embed(openai_client, args["query_text"])
        vlit = _vector_literal(vec)
        sql = (
            "SELECT title, category, "
            f"ROUND(VECTOR_DISTANCE(embedding, TO_VECTOR('{vlit}'), COSINE),4) AS distance, clause_text "
            "FROM policies "
            f"ORDER BY VECTOR_DISTANCE(embedding, TO_VECTOR('{vlit}'), COSINE) FETCH APPROX FIRST {k} ROWS ONLY"
        )
        return await mcp.run_sql(sql)

    if name == "run_sql":
        if not _is_readonly(args["sql"]):
            return "REFUSED: read-only SELECT queries only."
        return await mcp.run_sql(args["sql"])

    return json.dumps({"error": f"unknown tool {name}"})
