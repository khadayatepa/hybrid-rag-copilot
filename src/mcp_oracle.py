"""
Thin async bridge to Oracle's SQLcl MCP server (`sql -mcp`).

Generic and reused across projects: passes the full environment (TNS_ADMIN /
wallet), discovers tool arg names at runtime, tolerates SQLcl's connect quirk,
and always sends mcp_client/model. Requires SQLcl 25.2+ and a saved connection.
"""
from __future__ import annotations

import contextlib
import os
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MCP_CLIENT_ID = "hybrid-rag-copilot/0.1"
MCP_MODEL_ID = os.getenv("OPENAI_MODEL", "gpt-4o")


def _text_of(result: Any) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(text)
    return "\n".join(parts).strip() or "(no output)"


class OracleMCP:
    def __init__(self, command: str, connection_name: str):
        self._command = command
        self._connection_name = connection_name
        self._session: ClientSession | None = None
        self._schemas: dict[str, dict] = {}
        self._stack = contextlib.AsyncExitStack()

    async def __aenter__(self) -> "OracleMCP":
        params = StdioServerParameters(command=self._command, args=["-mcp"], env=dict(os.environ))
        try:
            read, write = await self._stack.enter_async_context(stdio_client(params))
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
            await self._session.initialize()
            listed = await self._session.list_tools()
            for tool in listed.tools:
                self._schemas[tool.name] = tool.inputSchema or {}
            await self._connect()
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    def _arg_name(self, tool: str, *hints: str) -> str:
        props: dict = self._schemas.get(tool, {}).get("properties", {})
        if not props:
            return hints[0]
        for hint in hints:
            for prop in props:
                if hint in prop.lower():
                    return prop
        return next(iter(props))

    async def _call(self, tool: str, args: dict[str, Any]) -> str:
        assert self._session is not None, "OracleMCP used outside its context"
        props = self._schemas.get(tool, {}).get("properties", {})
        payload = dict(args)
        if "mcp_client" in props:
            payload.setdefault("mcp_client", MCP_CLIENT_ID)
        if "model" in props:
            payload.setdefault("model", MCP_MODEL_ID)
        result = await self._session.call_tool(tool, payload)
        return _text_of(result)

    async def _connect(self) -> None:
        name_arg = self._arg_name("connect", "conn", "name")
        try:
            await self._call("connect", {name_arg: self._connection_name})
        except Exception:
            pass
        probe = await self.run_sql("SELECT user FROM dual")
        if any(m in probe for m in ("ORA-", "not established")) or "ERROR" in probe.upper():
            raise RuntimeError(f"SQLcl MCP failed to connect to '{self._connection_name}':\n{probe}")

    async def run_sql(self, sql: str) -> str:
        sql_arg = self._arg_name("run-sql", "sql", "query", "statement")
        return await self._call("run-sql", {sql_arg: sql})

    async def run_sqlcl(self, command: str) -> str:
        arg = self._arg_name("run-sqlcl", "sqlcl", "command")
        return await self._call("run-sqlcl", {arg: command})

    @property
    def tool_names(self) -> list[str]:
        return list(self._schemas)


@contextlib.asynccontextmanager
async def open_oracle_mcp(command: str, connection_name: str) -> AsyncIterator[OracleMCP]:
    async with OracleMCP(command, connection_name) as mcp:
        yield mcp
