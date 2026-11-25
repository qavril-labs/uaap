"""Build the model client a model-based bot needs from the kernel's settings (UAAP_* environment)."""

from __future__ import annotations

from uaap.config import Settings
from uaap.gateway.client import GatewayModelClient
from uaap.gateway.factory import build_model_registry, build_provider
from uaap.gateway.metering import InMemoryMeteringService
from uaap.gateway.registry import Router
from uaap.gateway.resilience import CircuitBreaker, ResilientCaller
from uaap.obs.audit import InMemoryAuditSink


def build_model_client(settings: Settings | None = None) -> GatewayModelClient:
    s = settings or Settings()
    caller = ResilientCaller(
        timeout_seconds=s.request_timeout_seconds,
        max_attempts=s.max_attempts,
        backoff_base_seconds=s.backoff_base_seconds,
        backoff_max_seconds=s.backoff_max_seconds,
        breaker=CircuitBreaker(
            failure_threshold=s.breaker_failure_threshold, cooldown_seconds=s.breaker_cooldown_seconds
        ),
    )
    return GatewayModelClient(
        provider=build_provider(s),
        router=Router(build_model_registry(s)),
        resilient_caller=caller,
        metering=InMemoryMeteringService(),
        audit_sink=InMemoryAuditSink(),
        requests_per_minute=s.requests_per_minute,
        daily_token_budget=s.daily_token_budget,
    )
