"""Run one bot once against a Core SaaS server. Identity comes from Core SaaS `/v1/me` (ADR-005)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import SecretStr

from uaap.agents.base import AgentInput, AgentReply
from uaap.bots.host import BotHost
from uaap.gateway.client import StructuredModel
from uaap.tools.adapters.core_saas import CoreSaasAdapter, register_core_saas_tools
from uaap.tools.types import CallerContext


async def run_bot(
    bot_id: str,
    message: str,
    *,
    core_url: str,
    token: str,
    company_id: str | None = None,
    today: str | None = None,
    adapter: CoreSaasAdapter | None = None,
    model: StructuredModel | None = None,
    host: BotHost | None = None,
) -> AgentReply:
    """Run one bot once. Model-based bots need `model`; deterministic bots run without one."""
    core = adapter or CoreSaasAdapter(core_url)
    host = host or BotHost(tools=lambda reg: register_core_saas_tools(reg, core), model=model)
    bot = host.build(bot_id)
    request_id = f"req_{uuid.uuid4().hex[:12]}"
    principal = await core.resolve_principal(token, request_id)
    caller = CallerContext(
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        permissions=principal.permissions,
        token=SecretStr(token),
        request_id=request_id,
    )
    business_date = today or datetime.now(UTC).date().isoformat()
    reply = await bot.respond(caller, AgentInput(message=message, today=business_date, company_id=company_id))
    await host.register_proposal(bot, caller, reply)
    return reply
