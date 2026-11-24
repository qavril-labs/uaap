"""Portable bots: the manifest matches each card, and a bot runs on a bare host with no service."""

from __future__ import annotations

from typing import Any

import pytest

from uaap.agents.base import AgentInput
from uaap.bots import BOTS, BotHost, get_spec
from uaap.errors import UaapError
from uaap.tools.adapters.core_saas import register_core_saas_tools

from .tools.fakes import ACCOUNTANT_TOKEN, COMPANY, FakeCoreSaas, build_stack, caller


class _StubModel:
    async def complete_json(self, **_: Any) -> dict[str, Any]:
        raise AssertionError("not called in these tests")


def _host(fake: FakeCoreSaas, *, model: Any = None) -> BotHost:
    _, adapter = build_stack(fake)
    return BotHost(tools=lambda reg: register_core_saas_tools(reg, adapter), model=model)


def test_manifest_matches_cards() -> None:
    assert len({b.id for b in BOTS}) == len(BOTS)
    for spec in BOTS:
        cls = spec.load()
        assert cls.card.agent_id == spec.id
        assert getattr(cls, "deterministic", False) == (spec.kind == "deterministic")
        assert spec.needs_model == (spec.kind == "agent")


def test_every_bot_builds_on_one_host() -> None:
    host = _host(FakeCoreSaas(), model=_StubModel())
    for spec in BOTS:
        assert host.build(spec.id).card.agent_id == spec.id
    assert host.build(BOTS[0].id) is host.build(BOTS[0].id)  # Idempotent.


def test_model_bot_without_model_is_refused() -> None:
    with pytest.raises(UaapError):
        _host(FakeCoreSaas()).build("universal-finance-agent")


def test_unknown_bot() -> None:
    with pytest.raises(UaapError):
        get_spec("nope")


async def test_deterministic_bot_runs_with_no_model() -> None:
    host = _host(FakeCoreSaas())
    bot = host.build("ledger-invariant-checker")
    reply = await bot.respond(
        caller(ACCOUNTANT_TOKEN, agent_id=None),
        AgentInput(message="Check the ledger.", today="2026-11-09", company_id=COMPANY),
    )
    assert reply.status == "answered"
    assert reply.data["findings"] == []


async def test_run_bot_end_to_end_against_fake_core() -> None:
    from uaap.bots.run import run_bot

    fake = FakeCoreSaas()
    _, adapter = build_stack(fake)
    reply = await run_bot(
        "ledger-invariant-checker",
        "Check the ledger.",
        core_url="http://core.test",
        token=ACCOUNTANT_TOKEN,
        company_id=COMPANY,
        today="2026-11-09",
        adapter=adapter,
    )
    assert reply.status == "answered"


async def test_mcp_lists_and_calls_deterministic_bots() -> None:
    from uaap.bots.mcp_server import handle
    from uaap.bots.run import run_bot

    _, adapter = build_stack(FakeCoreSaas())

    async def run(bot_id: str, message: str) -> Any:
        return await run_bot(
            bot_id, message, core_url="x", token=ACCOUNTANT_TOKEN, company_id=COMPANY,
            today="2026-11-09", adapter=adapter,
        )

    assert await handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, run) is None
    init = await handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, run)
    assert init and init["result"]["serverInfo"]["name"] == "uaap-bots"
    listed = await handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, run)
    assert listed and {t["name"] for t in listed["result"]["tools"]} == {
        "ledger-invariant-checker",
        "approval-reminder",
        "document-intelligence",
        "lead-nurturing",
    }
    call = await handle(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "ledger-invariant-checker", "arguments": {"message": "Check the ledger."}}},
        run,
    )
    assert call and call["result"]["isError"] is False
    bad = await handle(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "ticket-tess", "arguments": {}}}, run
    )
    assert bad and bad["error"]["code"] == -32602
