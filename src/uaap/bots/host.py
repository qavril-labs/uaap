"""BotHost: assembles the minimum kernel a bot needs, with in-memory defaults and pluggable parts."""

from __future__ import annotations

from collections.abc import Callable

from uaap.agents.base import AgentCatalog, BaseAgent
from uaap.approvals.inbox import ApprovalInbox
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


class BotHost:
    """One host serves any number of bots.

    `tools` registers the tool backend (Core SaaS adapter, a fake, or another system) into the
    registry. `model` is only required by bots that use one; deterministic bots run without it.
    """

    def __init__(
        self,
        *,
        tools: Callable[[ToolRegistry], None],
        model: StructuredModel | None = None,
        inbox: ApprovalInbox | None = None,
        audit_sink: AuditSink | None = None,
        max_tool_calls_per_run: int = 20,
    ) -> None:
        self.audit = audit_sink or InMemoryAuditSink()
        self.metrics = AgentMetrics()
        self.inbox = inbox or ApprovalInbox()
        self._model = model
        self.tools = ToolRegistry()
        tools(self.tools)
        self.agents = AgentRegistry()
        self.executor = ToolExecutor(
            self.tools, self.agents, audit_sink=self.audit, max_calls_per_run=max_tool_calls_per_run
        )
        self.approvals = ApprovalEngine(audit_sink=self.audit)
        self.runner = AgentRunner(
            InMemoryCheckpointStore(), self.approvals, audit_sink=self.audit, tool_executor=self.executor
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
