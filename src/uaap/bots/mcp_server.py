"""Expose bots as an MCP tool server over stdio (JSON-RPC 2.0, standard library only).

Run: `python -m uaap.bots mcp [--bot <id> ...]` with CORE_SAAS_URL and CORE_SAAS_TOKEN (and optional
CORE_SAAS_COMPANY_ID). One tool per bot, named after the bot id, so a bot repository can serve only itself.

Model-based bots are served only when a model is configured through the kernel's UAAP_* settings;
otherwise they are left out of the tool list rather than failing on first use. Identity comes from Core
SaaS, never from tool arguments. Each tool description carries the bot's purpose and two example requests,
which is all an orchestrator needs to route to it, so routing costs few tokens.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from uaap.bots.catalog import BotSpec, all_specs

# Nothing heavy is imported at module level: the handshake and the tool list are answered from the static
# catalog in milliseconds, and the kernel's runtime is loaded only when a bot is actually called.
if TYPE_CHECKING:
    from uaap.agents.base import AgentReply
    from uaap.gateway.client import StructuredModel

PROTOCOL_VERSION = "2024-11-05"
# run(bot_id, message, company_id=..., today=...) -> reply
Runner = Callable[..., Awaitable["AgentReply"]]


def _description(spec: BotSpec) -> str:
    examples = list(spec.examples) or [u.example for u in spec.load().card.use_cases[:2]]
    return f"{spec.name}: {spec.summary} Examples: " + " | ".join(examples)


def tool_list(specs: list[BotSpec]) -> list[dict[str, Any]]:
    return [
        {
            "name": b.id,
            "description": _description(b),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "maxLength": 4000},
                    "company_id": {"type": "string"},
                    "today": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        }
        for b in specs
    ]


def select_specs(bot_ids: list[str] | None, *, model_available: bool) -> list[BotSpec]:
    wanted = [b for b in all_specs() if not bot_ids or b.id in bot_ids]
    return [b for b in wanted if model_available or not b.needs_model]


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def handle(
    request: dict[str, Any], run: Runner, tools: list[dict[str, Any]] | None = None
) -> dict[str, Any] | None:
    """One JSON-RPC message in, one response out (None for notifications)."""
    tools = tools if tools is not None else tool_list(select_specs(None, model_available=False))
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
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}
    if method == "tools/call":
        params = request.get("params") or {}
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in {t["name"] for t in tools}:
            return _error(req_id, -32602, f"Unknown tool '{name}'")
        message = args.get("message")
        if not isinstance(message, str):
            return _error(req_id, -32602, "'message' must be a string")
        reply = await run(name, message, company_id=args.get("company_id"), today=args.get("today"))
        text = f"[{reply.status}] {reply.text}"
        structured: dict[str, Any] = {"status": reply.status}
        if reply.approval is not None:
            structured["approval_id"] = reply.approval.approval_id
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "structuredContent": structured,
                "isError": reply.status in ("failed", "refused"),
            },
        }
    return _error(req_id, -32601, f"Method not found: {method}")


def _model_from_env() -> StructuredModel | None:
    """A model client when the kernel's gateway is configured; otherwise None."""
    if os.environ.get("UAAP_PROVIDER_MODE") != "live":
        return None  # The fake provider is for tests; a real bot needs a live model.
    from uaap.bots.model import build_model_client

    return build_model_client()


async def serve(bot_ids: list[str] | None = None, model: StructuredModel | None = None) -> int:
    core_url, token = os.environ.get("CORE_SAAS_URL"), os.environ.get("CORE_SAAS_TOKEN")
    if not core_url or not token:
        print("Set CORE_SAAS_URL and CORE_SAAS_TOKEN", file=sys.stderr)
        return 1
    company = os.environ.get("CORE_SAAS_COMPANY_ID")
    model = model or _model_from_env()
    specs = select_specs(bot_ids, model_available=model is not None)
    if not specs:
        print("No bot to serve: unknown id, or the bot needs a model that is not configured", file=sys.stderr)
        return 1
    tools = tool_list(specs)

    cache: dict[str, Any] = {}

    def shared_host() -> Any:
        """One host for the life of the process, built on the first call so start-up stays fast."""
        if "host" not in cache:
            from uaap.bots.host import BotHost, durable_kwargs
            from uaap.tools.adapters.core_saas import CoreSaasAdapter, register_core_saas_tools

            adapter = CoreSaasAdapter(core_url)
            cache["adapter"] = adapter
            cache["host"] = BotHost(
                tools=lambda reg: register_core_saas_tools(reg, adapter), model=model, **durable_kwargs()
            )
        return cache["host"]

    async def run(
        bot_id: str, message: str, *, company_id: str | None = None, today: str | None = None
    ) -> AgentReply:
        import dataclasses

        from uaap.bots.run import run_bot

        host = shared_host()
        reply = await run_bot(
            bot_id,
            message,
            core_url=core_url,
            token=token,
            company_id=company_id or company,
            today=today,
            adapter=cache["adapter"],
            host=host,
        )
        if reply.status == "awaiting_approval":
            where = (
                "Decide it in the UAAP approvals inbox."
                if host.shared_inbox
                else "This server has no shared approvals inbox (UAAP_DATABASE_URL and "
                "UAAP_APPROVAL_TOKEN_SECRET are not set), so nobody can approve it."
            )
            reply = dataclasses.replace(reply, text=f"{reply.text} {where}")
        return reply

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response: dict[str, Any] | None = _error(None, -32700, "Parse error")
        else:
            ok = isinstance(request, dict)
            response = await handle(request, run, tools) if ok else _error(None, -32600, "Invalid request")
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0
