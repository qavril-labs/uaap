"""Expose the deterministic bots as an MCP tool server over stdio (JSON-RPC 2.0, standard library only).

Run: `python -m uaap.bots mcp` with CORE_SAAS_URL and CORE_SAAS_TOKEN (and optional CORE_SAAS_COMPANY_ID).
One tool per bot, named after the bot id. Model-based bots are not exposed: they need a model, which the
embedding application supplies through BotHost. Identity comes from Core SaaS, never from tool arguments.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from uaap.agents.base import AgentReply
from uaap.bots.catalog import all_specs
from uaap.bots.run import run_bot

PROTOCOL_VERSION = "2024-11-05"
Runner = Callable[[str, str], Awaitable[AgentReply]]


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "name": b.id,
            "description": f"{b.name}: {b.summary}",
            "inputSchema": {
                "type": "object",
                "properties": {"message": {"type": "string", "maxLength": 4000}},
                "required": ["message"],
                "additionalProperties": False,
            },
        }
        for b in all_specs()
        if not b.needs_model
    ]


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def handle(request: dict[str, Any], run: Runner) -> dict[str, Any] | None:
    """One JSON-RPC message in, one response out (None for notifications)."""
    method, req_id = request.get("method"), request.get("id")
    if req_id is None:
        return None  # Notifications such as notifications/initialized need no reply.
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "uaap-bots", "version": "0.1.0"},
        }
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": _tools()}}
    if method == "tools/call":
        params = request.get("params") or {}
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in {t["name"] for t in _tools()}:
            return _error(req_id, -32602, f"Unknown tool '{name}'")
        message = args.get("message")
        if not isinstance(message, str):
            return _error(req_id, -32602, "'message' must be a string")
        reply = await run(name, message)
        text = f"[{reply.status}] {reply.text}"
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"content": [{"type": "text", "text": text}], "isError": reply.status != "answered"},
        }
    return _error(req_id, -32601, f"Method not found: {method}")


async def serve() -> int:
    core_url, token = os.environ.get("CORE_SAAS_URL"), os.environ.get("CORE_SAAS_TOKEN")
    if not core_url or not token:
        print("Set CORE_SAAS_URL and CORE_SAAS_TOKEN", file=sys.stderr)
        return 1
    company = os.environ.get("CORE_SAAS_COMPANY_ID")

    async def run(bot_id: str, message: str) -> AgentReply:
        return await run_bot(bot_id, message, core_url=core_url, token=token, company_id=company)

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response: dict[str, Any] | None = _error(None, -32700, "Parse error")
        else:
            ok = isinstance(request, dict)
            response = await handle(request, run) if ok else _error(None, -32600, "Invalid request")
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0
