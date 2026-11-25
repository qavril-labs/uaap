"""A proposal made through an MCP bot server is decided in the UAAP service's approvals inbox.

Two separate hosts stand for two processes (the MCP server, and the UAAP service); only Postgres connects
them. Runs only with UAAP_TEST_DATABASE_URL (a database the owner role can migrate).
"""

from __future__ import annotations

import os

import pytest

from uaap.bots.host import BotHost
from uaap.bots.run import run_bot
from uaap.db.migrate import migrate
from uaap.db.state import PgState
from uaap.testing.fakes import (
    ACCOUNTANT_TOKEN,
    PLATFORM_APPROVER_TOKEN,
    PLATFORM_OPERATOR_TOKEN,
    FakeCoreSaas,
    build_stack,
    caller,
)
from uaap.tools.adapters.core_saas import register_core_saas_tools

URL = os.environ.get("UAAP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="UAAP_TEST_DATABASE_URL not set")
SECRET = b"m" * 40
LEAD = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _host(fake: FakeCoreSaas) -> tuple[BotHost, object]:
    assert URL
    _, adapter = build_stack(fake)
    host = BotHost(
        tools=lambda reg: register_core_saas_tools(reg, adapter),
        state=PgState(URL),
        token_secret=SECRET,
    )
    return host, adapter


async def test_proposal_from_an_mcp_server_is_decided_in_the_service_inbox() -> None:
    assert URL
    migrate(URL)
    fake = FakeCoreSaas()
    fake.leads = [
        {
            "id": LEAD, "name": "Ada Lovelace", "email": "ada@example.com", "company": "Analytical Ltd",
            "country": None, "interest": "demo", "industry": None, "message": None, "source": "landing",
            "createdAt": "2026-11-20T10:00:00.000Z", "status": "new",
            "consentAt": "2026-11-20T10:00:00.000Z", "lastDraftAt": None,
        }
    ]

    # Process 1: the MCP server runs the bot, which proposes a draft. It files it in the shared inbox.
    mcp_host, mcp_adapter = _host(fake)
    reply = await run_bot(
        "lead-nurturing",
        f"Draft a follow-up for lead {LEAD}",
        core_url="x",
        token=PLATFORM_OPERATOR_TOKEN,
        today="2026-11-25",
        adapter=mcp_adapter,  # type: ignore[arg-type]
        host=mcp_host,
    )
    assert reply.status == "awaiting_approval" and reply.approval is not None, reply.text
    assert fake.drafts == []  # Nothing is saved before a person approves.
    approval_id, tenant = reply.approval.approval_id, reply.approval.tenant_id
    del mcp_host  # The MCP process is gone; nothing in memory carries over.

    # Process 2: the UAAP service sees it in the inbox and a different person approves it.
    service_host, _ = _host(fake)
    pending = [i for i in service_host.inbox.list(tenant, "PENDING") if i.approval_id == approval_id]
    assert len(pending) == 1, "the proposal never reached the shared inbox"
    item, token = service_host.inbox.claim(tenant, approval_id)
    bot = service_host.build("lead-nurturing")
    saved = await bot.approve(
        caller(PLATFORM_APPROVER_TOKEN, agent_id=None),
        run_id=item.run_id,
        approval_id=item.approval_id,
        approval_token=token,
        confirmed_payload=item.payload,
    )
    assert saved.status == "answered", saved.text
    service_host.inbox.resolve(item, "APPROVED", "user-ops-2", "answered")
    assert len(fake.drafts) == 1 and fake.drafts[0]["leadId"] == LEAD


async def test_without_a_database_a_host_reports_no_shared_inbox() -> None:
    _, adapter = build_stack(FakeCoreSaas())
    host = BotHost(tools=lambda reg: register_core_saas_tools(reg, adapter))
    assert host.shared_inbox is False
    assert ACCOUNTANT_TOKEN  # (kept so the shared fakes import stays exercised)
