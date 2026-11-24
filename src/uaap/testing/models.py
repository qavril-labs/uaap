"""A model double that returns scripted plans, validated against the agent's schema like the gateway does."""

from __future__ import annotations

from typing import Any

import jsonschema

from uaap.gateway.types import Message


class ScriptedModel:
    """Returns scripted plans, validated against the agent's schema like the real gateway does."""

    def __init__(self, *plans: dict[str, Any] | Exception) -> None:
        self.plans = list(plans)
        self.calls: list[tuple[Message, ...]] = []

    async def complete_json(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        request_id: str,
        messages: tuple[Message, ...],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(messages)
        plan = self.plans.pop(0)
        if isinstance(plan, Exception):
            raise plan
        jsonschema.validate(plan, schema)
        return plan
