"""Document Intelligence (B7): extract invoice fields from text supplied in the request.

Deterministic: fixed parsing rules and decimal arithmetic, no model. Every value in the reply is copied
from the document text or is a stated calculation (line total, sum of lines, subtotal plus tax). Nothing
is guessed: a field that is absent or unreadable is named as missing and the result is PARTIAL.

Readiness: READY (every required field present and every check passes), PARTIAL (something is missing
or a check fails; each is named), BLOCKED (no invoice fields could be read at all).

Out of scope for this version: images, PDFs and OCR (they need file storage, PLT-11), contracts, and any
posting of what was read. Supported layout (one item per line, ISO dates only):

    Supplier: Acme Ltd
    Invoice No: INV-2041
    Date: 2026-11-03
    Currency: USD
    Paper 10 x 2.50 = 25.00
    Subtotal: 25.00
    Tax: 2.50
    Total: 27.50
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from uaap.agents.base import AgentCard, AgentInput, AgentReply, CaseStory, EdgeCase, Kpi, Trace, UseCase
from uaap.agents.deterministic import DeterministicBot
from uaap.agents.guardrails import MANDATORY_GUARDRAILS
from uaap.runtime.runner import AgentRunner
from uaap.tools.agents import AgentPrincipal
from uaap.tools.types import CallerContext

AGENT_ID = "document-intelligence"

PRINCIPAL = AgentPrincipal(
    agent_id=AGENT_ID, display_name="Document Intelligence", grants=frozenset(), tools=frozenset()
)

_ERRORS: dict[str, str] = {
    "INTERNAL": "Something went wrong on our side. Nothing was saved or posted. Please try again.",
    "VALIDATION": "I could not use that request as written. Please check it and try again.",
    "UNAUTHENTICATED": "Your session has ended. Please sign in again.",
    "FORBIDDEN": "You do not have permission to read documents.",
    "NOT_FOUND": "I could not find that document.",
    "CONFLICT": "The document changed while I was reading it. Please try again.",
    "TOOL_BLOCKED": "That action is not allowed for agents.",
    "RATE_LIMITED": "Too many requests right now. Please try again shortly.",
    "QUOTA_EXCEEDED": "The usage limit for this period has been reached. Please try again later.",
    "UPSTREAM_FAILED": "A required service is unavailable. Nothing was saved or posted.",
    "UPSTREAM_TIMEOUT": "A required service took too long to respond. Please try again.",
    "UPSTREAM_INVALID_RESPONSE": "A service returned data I could not verify, so I have not used it.",
    "UNGROUNDED_REPLY": "I could not verify my reading against the document, so I have not shown it.",
    "AGENT_DISABLED": "Document reading has been switched off by an administrator.",
}

_NUM = r"\d[\d,]*(?:\.\d+)?"
_LINE = re.compile(
    rf"^(?P<desc>.+?)\s+(?P<qty>{_NUM})\s*[x×]\s*(?P<unit>{_NUM})\s*=\s*(?P<amount>{_NUM})\s*$"
)
_FIELD = {
    "supplier": re.compile(r"^\s*(?:supplier|vendor|from)\s*:\s*(?P<v>.+?)\s*$", re.I | re.M),
    "invoice_number": re.compile(
        r"^\s*invoice\s*(?:no\.?|number|#)?\s*[:#]\s*(?P<v>[A-Za-z0-9][A-Za-z0-9\-/]*)\s*$", re.I | re.M
    ),
    "invoice_date": re.compile(r"^\s*(?:invoice\s+)?date\s*:\s*(?P<v>\d{4}-\d{2}-\d{2})\s*$", re.I | re.M),
    "currency": re.compile(r"^\s*(?i:currency)\s*:\s*(?P<v>[A-Z]{3})\s*$", re.M),
}
_AMOUNT = {
    "subtotal": re.compile(rf"^\s*sub\s*-?total\s*:\s*(?P<v>{_NUM})\s*$", re.I | re.M),
    "tax": re.compile(rf"^\s*(?:tax|vat|gst)\s*:\s*(?P<v>{_NUM})\s*$", re.I | re.M),
    "total": re.compile(rf"^\s*(?:grand\s+)?total\s*:\s*(?P<v>{_NUM})\s*$", re.I | re.M),
}
REQUIRED = ("supplier", "invoice_number", "invoice_date", "currency", "total")


def _dec(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


@dataclass(frozen=True)
class Extraction:
    fields: dict[str, str]
    lines: list[dict[str, str]]
    missing: list[str]
    problems: list[str]  # Failed arithmetic checks, each naming both sides.
    computed: list[Decimal]  # Values produced by a calculation, so the reply may cite them.

    @property
    def readiness(self) -> str:
        if not self.fields and not self.lines:
            return "BLOCKED"
        return "READY" if not self.missing and not self.problems else "PARTIAL"


def extract_invoice(text: str) -> Extraction:
    """Deterministic. Same text in, same result out."""
    fields: dict[str, str] = {}
    for name, pattern in {**_FIELD, **_AMOUNT}.items():
        m = pattern.search(text)
        if m:
            fields[name] = m.group("v").strip()

    lines: list[dict[str, str]] = []
    for raw in text.splitlines():
        m = _LINE.match(raw.strip())
        if m:
            lines.append({k: m.group(k).strip() for k in ("desc", "qty", "unit", "amount")})

    missing = [name for name in REQUIRED if name not in fields]
    if not lines:
        missing.append("lines")

    problems: list[str] = []
    computed: list[Decimal] = []
    line_sum = Decimal(0)
    for ln in lines:
        qty, unit, amount = _dec(ln["qty"]), _dec(ln["unit"]), _dec(ln["amount"])
        if qty is None or unit is None or amount is None:
            continue
        line_sum += amount
        if qty * unit != amount:
            problems.append(f"{ln['desc']}: {ln['qty']} x {ln['unit']} is {qty * unit}, not {ln['amount']}")
            computed.append(qty * unit)
    subtotal = _dec(fields["subtotal"]) if "subtotal" in fields else None
    tax = _dec(fields["tax"]) if "tax" in fields else None
    total = _dec(fields["total"]) if "total" in fields else None
    if lines and subtotal is not None and line_sum != subtotal:
        problems.append(f"the lines add up to {line_sum}, but the subtotal says {fields['subtotal']}")
        computed.append(line_sum)
    if subtotal is not None and tax is not None and total is not None and subtotal + tax != total:
        problems.append(f"subtotal plus tax is {subtotal + tax}, but the total says {fields['total']}")
        computed.append(subtotal + tax)
    return Extraction(fields, lines, missing, problems, computed)


CARD = AgentCard(
    agent_id=AGENT_ID,
    name="Document Intelligence",
    version="1.0.0",
    owner="Platform team",
    purpose=(
        "Read the fields of an invoice from text the user supplies and check its arithmetic. It reports "
        "what is missing instead of guessing, and it saves or posts nothing."
    ),
    use_cases=(
        UseCase(
            "Read an invoice",
            "Extract the fields from this invoice: <invoice text>",
            "Supplier, invoice number, date, currency, lines and totals, with readiness.",
            None,
            None,
        ),
        UseCase(
            "Check an invoice's arithmetic",
            "Do the totals on this invoice add up? <invoice text>",
            "Each failed check names both sides of the calculation.",
            None,
            None,
        ),
    ),
    case_stories=(
        CaseStory(
            title="A clean supplier invoice",
            actor="Accounts payable clerk",
            situation="A clerk pastes a supplier invoice and wants its fields before entering it.",
            steps=(
                "The clerk pastes the invoice text.",
                "Every required field is found and the lines, subtotal, tax and total agree.",
            ),
            outcome="The clerk sees READY with the fields to review; nothing is saved.",
        ),
        CaseStory(
            title="An invoice that does not add up",
            actor="Accounts payable clerk",
            situation="The total on a supplier invoice looks wrong.",
            steps=(
                "The clerk pastes the invoice text.",
                "The check finds the lines do not add up to the subtotal and says by how much.",
            ),
            outcome="The clerk queries the supplier before any entry is made.",
        ),
    ),
    edge_cases=(
        EdgeCase("Complete, consistent invoice", "READY with every field.", "test_clean_invoice_is_ready"),
        EdgeCase("Required field absent", "PARTIAL naming the field.", "test_missing_field_is_partial"),
        EdgeCase(
            "Line quantity times price wrong", "PARTIAL naming the line.", "test_line_arithmetic_is_checked"
        ),
        EdgeCase(
            "Lines do not add to subtotal",
            "PARTIAL stating both sides.",
            "test_subtotal_mismatch_is_reported",
        ),
        EdgeCase(
            "Subtotal plus tax differs from total",
            "PARTIAL stating both sides.",
            "test_total_mismatch_is_reported",
        ),
        EdgeCase("Text with no invoice fields", "BLOCKED; nothing invented.", "test_non_invoice_is_blocked"),
        EdgeCase(
            "Half-cent amounts", "Decimal arithmetic, no rounding drift.", "test_decimal_arithmetic_is_exact"
        ),
        EdgeCase("Same text twice", "Identical result.", "test_extraction_is_deterministic"),
    ),
    out_of_scope=(
        "Images, PDFs and OCR (need file storage, PLT-11)",
        "Contracts and any document other than an invoice",
        "Saving, posting or paying anything that was read",
        "Guessing a missing field or correcting a wrong amount",
    ),
    guardrails=MANDATORY_GUARDRAILS,
    error_handling=_ERRORS,
    kpis=(
        Kpi(
            "Fields read correctly",
            "Share of required fields that match a reviewer's reading on the evaluation set.",
            "Every required field present in the document is read exactly",
        ),
    ),
    data_sources=("The invoice text in the user's request",),
)


class DocumentIntelligence(DeterministicBot):
    card = CARD
    principal = PRINCIPAL

    def __init__(self, runner: AgentRunner, **kwargs: Any) -> None:
        super().__init__(runner, **kwargs)

    async def _handle(self, caller: CallerContext, inp: AgentInput, trace: Trace) -> AgentReply:
        result = extract_invoice(inp.message)
        for value in result.computed:
            trace.sources.add(str(value))  # Deterministic calculation: cited so it can be stated.
        data: dict[str, Any] = {
            "readiness": result.readiness,
            "fields": result.fields,
            "lines": result.lines,
            "missing": result.missing,
            "problems": result.problems,
        }
        if result.readiness == "BLOCKED":
            return AgentReply(
                "needs_clarification",
                "BLOCKED: I could not read any invoice fields. Paste the invoice text, one item per line.",
                data=data,
            )
        out = [f"{result.readiness}: invoice fields read from the text."]
        out += [f"- {name}: {value}" for name, value in result.fields.items()]
        out += [f"- item: {ln['desc']}, {ln['qty']} x {ln['unit']} = {ln['amount']}" for ln in result.lines]
        if result.missing:
            out.append("Missing: " + ", ".join(result.missing))
        out += [f"Check failed: {p}" for p in result.problems]
        return AgentReply("answered", "\n".join(out), data=data)
