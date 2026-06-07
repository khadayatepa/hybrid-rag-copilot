# Hybrid-RAG Refund Copilot — one query joins vectors + tables on Oracle 26ai

A refund-compliance copilot that answers a question needing **both** unstructured
and structured data — in a **single Oracle 26ai query**. It vector-searches the
refund-policy clauses *and* joins the live order facts together (relational JOIN +
`VECTOR_DISTANCE` via `CROSS JOIN LATERAL`), then an agent renders the decision.

```
 refund request ─> assess_refund  ──(one hybrid SQL)──>  Oracle 26ai
                   (policy vectors + order JOIN)              │
                                                             ▼
                                          copilot decision: APPROVE / DENY / ESCALATE
```

This is the "why 26ai" story: vectors and operational data live in **one database**,
so a single statement answers questions a bolt-on vector store can't.

## Why it's interesting
A refund decision needs the *meaning* of the customer's reason matched to policy
(vector search) **and** hard facts like days-since-delivery (relational). Most stacks
need two systems and glue code. Here it's one `SELECT`.

## Prerequisites
- Oracle 23ai/26ai with vectors; **SQLcl 25.2+** with a saved connection
  (`conn -save DEBATE -savepwd debate@<tns-alias>`); Python 3.10+; an OpenAI key.

## Setup
```powershell
pip install -r requirements.txt
copy .env.example .env          # set OPENAI_API_KEY + ORACLE_MCP_CONNECTION
python src/seed.py              # policies (+embeddings), orders, refund_requests
python src/copilot.py           # assess REQUEST_ID and render a decision
streamlit run src/dashboard.py  # view decisions
```

## Files
| File | Purpose |
| --- | --- |
| `sql/schema.sql` | Reference DDL + the hybrid query. |
| `src/seed.py` | Loads policies (vector), orders, requests via MCP. |
| `src/tools.py` | `assess_refund` (the hybrid query), `policy_search`, `run_sql`. |
| `src/copilot.py` | Single agent: decide APPROVE / DENY / ESCALATE. |
| `src/dashboard.py` | Streamlit view of decisions. |

## Seeded scenarios
- **7001** — "changed my mind," delivered ~50+ days ago → **DENY** (outside 30-day window).
- **7002** — "arrived with cracked blades" → **APPROVE** (damaged-on-arrival clause).
- **7003** — e-book already downloaded → **DENY** (digital-goods clause).

Set `REQUEST_ID` in `.env` to switch.

> ⚠️ A learning demo — not legal/financial advice.
