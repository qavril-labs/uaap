"""Document Intelligence: deterministic invoice reading; reports what is missing, never guesses."""

from __future__ import annotations

from uaap.agents.base import AgentCatalog, AgentInput
from uaap.agents.document_intelligence import DocumentIntelligence, extract_invoice
from uaap.obs.metrics import AgentMetrics
from uaap.runtime.approval.hooks import ApprovalEngine
from uaap.runtime.checkpointer.in_memory import InMemoryCheckpointStore
from uaap.runtime.runner import AgentRunner

from ..tools.fakes import ACCOUNTANT_TOKEN, FakeCoreSaas, build_stack, caller

INVOICE = """Supplier: Acme Ltd
Invoice No: INV-2041
Date: 2026-11-03
Currency: USD
Paper 10 x 2.50 = 25.00
Ink 2 x 7.50 = 15.00
Subtotal: 40.00
Tax: 4.00
Total: 44.00
"""


def _bot() -> DocumentIntelligence:
    executor, _ = build_stack(FakeCoreSaas())
    runner = AgentRunner(InMemoryCheckpointStore(), ApprovalEngine(), tool_executor=executor)
    bot = DocumentIntelligence(runner, metrics=AgentMetrics())
    AgentCatalog(executor.agents, executor.registry).add(bot)
    return bot


async def _run(text: str):  # type: ignore[no-untyped-def]
    return await _bot().respond(
        caller(ACCOUNTANT_TOKEN, agent_id=None), AgentInput(message=text, today="2026-11-09")
    )


async def test_clean_invoice_is_ready() -> None:
    reply = await _run(INVOICE)
    assert reply.status == "answered"
    assert reply.data["readiness"] == "READY"
    assert reply.data["fields"]["invoice_number"] == "INV-2041"
    assert reply.data["fields"]["total"] == "44.00"
    assert len(reply.data["lines"]) == 2


async def test_missing_field_is_partial() -> None:
    reply = await _run(INVOICE.replace("Currency: USD\n", ""))
    assert reply.data["readiness"] == "PARTIAL"
    assert reply.data["missing"] == ["currency"]
    assert "Missing: currency" in reply.text


async def test_line_arithmetic_is_checked() -> None:
    reply = await _run(INVOICE.replace("10 x 2.50 = 25.00", "10 x 2.50 = 26.00"))
    assert reply.data["readiness"] == "PARTIAL"
    assert any(p.startswith("Paper:") for p in reply.data["problems"])
    assert "UNGROUNDED_REPLY" not in reply.guardrail_events


async def test_subtotal_mismatch_is_reported() -> None:
    text = INVOICE.replace("Subtotal: 40.00", "Subtotal: 41.00").replace("Total: 44.00", "Total: 45.00")
    reply = await _run(text)
    assert reply.data["readiness"] == "PARTIAL"
    assert "the lines add up to 40.00, but the subtotal says 41.00" in reply.text


async def test_total_mismatch_is_reported() -> None:
    reply = await _run(INVOICE.replace("Total: 44.00", "Total: 50.00"))
    assert reply.data["readiness"] == "PARTIAL"
    assert "subtotal plus tax is 44.00, but the total says 50.00" in reply.text
    assert "UNGROUNDED_REPLY" not in reply.guardrail_events


async def test_non_invoice_is_blocked() -> None:
    reply = await _run("Hello, can you help me with my holiday plans?")
    assert reply.status == "needs_clarification"
    assert reply.data["readiness"] == "BLOCKED"
    assert reply.data["fields"] == {}


def test_decimal_arithmetic_is_exact() -> None:
    text = INVOICE.replace("Paper 10 x 2.50 = 25.00", "Paper 3 x 0.005 = 0.015").replace(
        "Subtotal: 40.00", "Subtotal: 15.015"
    )
    result = extract_invoice(text)
    assert not any(p.startswith("Paper") for p in result.problems)  # 3 x 0.005 is exactly 0.015


def test_extraction_is_deterministic() -> None:
    assert extract_invoice(INVOICE) == extract_invoice(INVOICE)
