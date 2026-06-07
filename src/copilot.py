"""
Refund-compliance copilot — a single agent that answers a question needing BOTH
unstructured policy and structured order data, using one hybrid Oracle query.

    Refund request ─> assess_refund (vector policy search + relational JOIN, one SQL)
                   ─> copilot reasons ─> decision (APPROVE / DENY / ESCALATE) + rationale

Run:  python src/copilot.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import textwrap
from typing import Any

from openai import AsyncOpenAI

import config
import persist
from mcp_oracle import open_oracle_mcp, OracleMCP
from tools import TOOL_SPECS, dispatch

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

SYSTEM = (
    "You are a refund-compliance officer. Decide whether a customer's refund request is allowed "
    "under company policy. You are given hybrid evidence that combines the order's facts with the "
    "most relevant policy clauses (retrieved by meaning). Reason strictly from the order facts and "
    "the cited policy. Quote the specific clause and the deciding fact (e.g. days since delivery). "
    "If the situation is ambiguous or smells like abuse, escalate. End your answer with a line "
    "exactly like:  DECISION: APPROVE  (or DENY, or ESCALATE)."
)

MAX_TOOL_TURNS = 6


def _wrap(t: str) -> str:
    return "\n".join(textwrap.fill(p, 92, initial_indent="   ", subsequent_indent="   ") if p.strip() else "" for p in t.splitlines())


async def run_agent(client: AsyncOpenAI, mcp: OracleMCP, user_prompt: str) -> str:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user_prompt},
    ]
    for _ in range(MAX_TOOL_TURNS):
        resp = await client.chat.completions.create(
            model=config.OPENAI_MODEL, messages=messages, tools=TOOL_SPECS, temperature=0.2)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return (msg.content or "").strip()
        messages.append({"role": "assistant", "content": msg.content,
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            print(f"      [copilot → {tc.function.name}] {(args.get('sql') or args.get('query_text') or args.get('request_id') or '')}".__str__()[:90])
            try:
                result = await dispatch(tc.function.name, args, mcp=mcp, openai_client=client)
            except Exception as exc:
                result = f"ERROR: {exc}"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result[:6000]})
    return "(copilot exhausted its tool budget)"


def _parse_decision(text: str) -> str:
    m = re.search(r"DECISION:\s*(APPROVE|DENY|ESCALATE)", text.upper())
    return m.group(1) if m else "UNKNOWN"


async def main() -> None:
    rid = config.REQUEST_ID
    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY or None)
    print(f"\n=== Refund-compliance copilot — request {rid} ===\n")

    async with open_oracle_mcp(config.SQLCL_COMMAND, config.ORACLE_MCP_CONNECTION) as mcp:
        print(f"Connected via SQLcl MCP. Tools: {', '.join(mcp.tool_names)}\n")
        print("Running the hybrid query (vector policy search + relational JOIN, one SQL)...")
        evidence = await dispatch("assess_refund", {"request_id": rid}, mcp=mcp, openai_client=client)
        print(_wrap(evidence[:900]) + "\n")

        rationale = await run_agent(client, mcp,
            f"Assess refund request_id = {rid}.\n\nHybrid evidence (order facts + nearest policy clauses):\n{evidence}\n\nDecide.")
        decision = _parse_decision(rationale)
        print(f"\n⚖️  DECISION: {decision}\n")
        print(_wrap(rationale) + "\n")

        await persist.ensure_tables(mcp)
        import time
        await persist.save_decision(mcp, run_id=int(time.time()), request_id=rid,
                                    model=config.OPENAI_MODEL, decision=decision,
                                    evidence=evidence, rationale=rationale)
        print("💾 Saved to copilot_runs (view: v_copilot_feed).")


if __name__ == "__main__":
    asyncio.run(main())
