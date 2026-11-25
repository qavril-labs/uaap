"""The manifest of every portable bot. A new bot is added here and nowhere else in the host."""

from __future__ import annotations

import importlib
import importlib.metadata
from dataclasses import dataclass
from typing import Literal

from uaap.errors import UaapError


@dataclass(frozen=True)
class BotSpec:
    id: str  # Equals the agent card's agent_id; the catalog test enforces it.
    name: str
    version: str
    kind: Literal["agent", "deterministic"]
    module: str
    class_name: str
    needs_model: bool
    needs_inbox: bool = False
    # Tools the bot is granted. Read from the class at load time; the manifest copy is for listing.
    summary: str = ""
    # Two example requests, so a tool list (and a router) needs no imports of the bot itself.
    examples: tuple[str, ...] = ()

    def load(self) -> type:
        return getattr(importlib.import_module(self.module), self.class_name)  # type: ignore[no-any-return]


BOTS: tuple[BotSpec, ...] = (
    BotSpec(
        "universal-finance-agent",
        "Universal Finance Agent",
        "0.1.0",
        "agent",
        "uaap.agents.finance",
        "UniversalFinanceAgent",
        needs_model=True,
        summary="Ledger questions; drafts journal entries and reversals for approval.",
        examples=(
            "What does the trial balance look like as of 2026-11-30?",
            "Accrue November rent: debit 6100, credit 2100, USD 1,250.00 on 2026-11-30.",
        ),
    ),
    BotSpec(
        "approval-agent",
        "Approval Agent",
        "0.1.0",
        "agent",
        "uaap.agents.approval",
        "ApprovalAgent",
        needs_model=True,
        needs_inbox=True,
        summary="Reads the approvals inbox; never approves or rejects.",
        examples=(
            "What is waiting for my approval?",
            "Which approvals expire in the next day?",
        ),
    ),
    BotSpec(
        "ledger-invariant-checker",
        "Ledger Invariant Checker",
        "0.1.0",
        "deterministic",
        "uaap.agents.ledger_invariants",
        "LedgerInvariantChecker",
        needs_model=False,
        summary="Checks the ledger balances; reports only.",
        examples=(
            "Check the ledger as of 2026-11-30.",
            "Does the ledger balance before I close the period?",
        ),
    ),
    BotSpec(
        "approval-reminder",
        "Approval Reminder",
        "0.1.0",
        "deterministic",
        "uaap.agents.approval_reminder",
        "ApprovalReminder",
        needs_model=False,
        needs_inbox=True,
        summary="Flags approvals about to expire or waiting too long.",
        examples=(
            "Which approvals need a reminder?",
            "Is anything about to lapse?",
        ),
    ),
    BotSpec(
        "onboard-ola",
        "Onboard Ola",
        "0.1.0",
        "agent",
        "uaap.agents.onboard_ola",
        "OnboardOla",
        needs_model=True,
        summary="Onboarding checklists, equipment and access proposals.",
        examples=(
            "Create first-week onboarding checklist for Jane Doe starting in finance.",
            "Propose system access for John Smith in engineering.",
        ),
    ),
    BotSpec(
        "ticket-tess",
        "Ticket Tess",
        "0.1.0",
        "agent",
        "uaap.agents.ticket_tess",
        "TicketTess",
        needs_model=True,
        summary="Answers from the verified knowledge base; hands over to humans.",
        examples=(
            "How do I reset my password?",
            "What is the status of ticket TIK-1001?",
        ),
    ),
    BotSpec(
        "document-intelligence",
        "Document Intelligence",
        "0.1.0",
        "deterministic",
        "uaap.agents.document_intelligence",
        "DocumentIntelligence",
        needs_model=False,
        summary="Reads invoice fields from supplied text and checks the arithmetic.",
        examples=(
            "Extract the fields from this invoice: <invoice text>",
            "Do the totals on this invoice add up? <invoice text>",
        ),
    ),
    BotSpec(
        "lead-nurturing",
        "Lead Nurturing",
        "0.1.0",
        "deterministic",
        "uaap.agents.lead_nurturing",
        "LeadNurturing",
        needs_model=False,
        summary="Flags marketing leads needing follow-up; drafts one for approval. Platform workspace only.",
        examples=(
            "Which leads need a follow-up?",
            "Draft a follow-up for lead <lead id>",
        ),
    ),
)


ENTRY_POINT_GROUP = "uaap.bots"


def external_specs() -> tuple[BotSpec, ...]:
    """Bots installed from other packages. Each declares a BotSpec under the `uaap.bots` entry point.

    A built-in bot with the same id wins, so installing a bot's own package never replaces the core one.
    """
    builtin = {b.id for b in BOTS}
    found: dict[str, BotSpec] = {}
    for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
        spec = ep.load()
        if isinstance(spec, BotSpec) and spec.id not in builtin:
            found[spec.id] = spec
    return tuple(found[k] for k in sorted(found))


def all_specs() -> tuple[BotSpec, ...]:
    return BOTS + external_specs()


def get_spec(bot_id: str) -> BotSpec:
    for spec in all_specs():
        if spec.id == bot_id:
            return spec
    raise UaapError("NOT_FOUND", f"Unknown bot '{bot_id}'", {"known": [b.id for b in BOTS]})
