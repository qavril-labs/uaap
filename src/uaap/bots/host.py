"""BotHost: assembles the minimum kernel a bot needs, with in-memory defaults and pluggable parts."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from uaap.agents.base import AgentCatalog, AgentReply, BaseAgent
from uaap.approvals.inbox import ApprovalInbox, InboxItem
from uaap.bots.catalog import get_spec
from uaap.errors import UaapError
from uaap.gateway.client import StructuredModel
from uaap.obs.audit import AuditSink, InMemoryAuditSink
from uaap.obs.metrics import AgentMetrics
from uaap.runtime.approval.hooks import ApprovalEngine
from uaap.runtime.checkpointer.in_memory import InMemoryCheckpointStore
from uaap.runtime.runner import AgentRunner
from uaap.tools.agents import AgentRegistry
from uaap.tools.executor import ToolExecutor
from uaap.tools.registry import ToolRegistry
from uaap.tools.types import CallerContext


class BotHost:
    """One host serves any number of bots.

    `tools` registers the tool backend (Core SaaS adapter, a fake, or another system) into the
    registry. `model` is only required by bots that use one; deterministic bots run without it.

    Without `state` everything is in memory: a proposal can only be decided inside this process. With
    `state` (a PgState on the UAAP database) runs, approvals, the inbox and idempotency keys are stored
    in the shared database, so a person can decide the proposal in the UAAP service's approvals inbox.
    """

    def __init__(
        self,
        *,
        tools: Callable[[ToolRegistry], None],
        model: StructuredModel | None = None,
        inbox: ApprovalInbox | None = None,
        audit_sink: AuditSink | None = None,
        max_tool_calls_per_run: int = 20,
        state: Any | None = None,
        token_secret: bytes | None = None,
        approval_ttl_seconds: int = 3600,
    ) -> None:
        self.audit = audit_sink or InMemoryAuditSink()
        self.metrics = AgentMetrics()
        self.shared_inbox = state is not None
        self._model = model
        self.tools = ToolRegistry()
        tools(self.tools)
        self.agents = AgentRegistry()
        if state is None:
            self.executor = ToolExecutor(
                self.tools, self.agents, audit_sink=self.audit, max_calls_per_run=max_tool_calls_per_run
            )
            self.approvals = ApprovalEngine(audit_sink=self.audit)
            self.inbox = inbox or ApprovalInbox()
            checkpoints: Any = InMemoryCheckpointStore()
        else:
            from uaap.db.durable import (
                DurableCheckpointStore,
                DurableIdempotencyStore,
                PgApprovalRecords,
                PgInboxHistory,
            )

            self.executor = ToolExecutor(
                self.tools,
                self.agents,
                idempotency=DurableIdempotencyStore(state),
                audit_sink=self.audit,
                max_calls_per_run=max_tool_calls_per_run,
            )
            self.approvals = ApprovalEngine(
                audit_sink=self.audit, records=PgApprovalRecords(state), token_secret=token_secret
            )
            self.inbox = inbox or ApprovalInbox(
                history=PgInboxHistory(state), token_for=self.approvals.token_for if token_secret else None
            )
            checkpoints = DurableCheckpointStore(state)
        self.runner = AgentRunner(
            checkpoints,
            self.approvals,
            audit_sink=self.audit,
            tool_executor=self.executor,
            approval_ttl_seconds=approval_ttl_seconds,
        )
        self._catalog = AgentCatalog(self.agents, self.tools)

    def build(self, bot_id: str) -> BaseAgent:
        """Build, validate against the agent standard and register one bot. Idempotent per host."""
        existing = self._catalog.get(bot_id)
        if existing is not None:
            return existing
        spec = get_spec(bot_id)
        if spec.needs_model and self._model is None:
            raise UaapError("VALIDATION", f"Bot '{bot_id}' needs a model; pass model= to BotHost")
        cls = spec.load()
        kwargs: dict[str, object] = {"metrics": self.metrics, "audit_sink": self.audit}
        args: list[object] = [self._model] if spec.needs_model else []
        args.append(self.runner)
        if spec.needs_inbox:
            args.append(self.inbox)
        bot = cls(*args, **kwargs)
        self._catalog.add(bot)
        return bot  # type: ignore[no-any-return]

    async def register_proposal(self, bot: BaseAgent, caller: CallerContext, reply: AgentReply) -> None:
        """File a proposal in the approvals inbox, as the HTTP service does, so a person decides it there."""
        if reply.status != "awaiting_approval" or reply.approval is None or not reply.approval_token:
            return
        a = reply.approval
        await asyncio.to_thread(
            self.inbox.add,
            InboxItem(
                approval_id=a.approval_id,
                tenant_id=caller.tenant_id,
                run_id=a.run_id,
                agent_id=bot.card.agent_id,
                initiator_id=caller.actor_id,
                action=a.action_name,
                summary=a.summary,
                payload=a.proposed_payload,
                payload_hash=a.payload_hash,
                created_at=a.created_at,
                expires_at=a.expires_at,
            ),
            reply.approval_token,
        )


def durable_kwargs(settings: Any | None = None) -> dict[str, Any]:
    """BotHost keyword arguments that put approvals in the shared UAAP database, when one is configured."""
    from uaap.config import Settings

    s = settings or Settings()
    if s.database_url is None or s.approval_token_secret is None:
        return {}
    from uaap.db.state import PgState
    from uaap.db.stores import PostgresAuditSink, PostgresWriter

    url = s.database_url.get_secret_value()
    return {
        "state": PgState(url),
        "token_secret": s.approval_token_secret.get_secret_value().encode("utf-8"),
        "audit_sink": PostgresAuditSink(PostgresWriter(url)),
        "approval_ttl_seconds": s.approval_ttl_seconds,
    }
