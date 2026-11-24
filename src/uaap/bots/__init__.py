"""Portable bots: each bot is described by a manifest and can be built and run on its own.

    from uaap.bots import BotHost, BOTS
    host = BotHost(tools=my_register_function, model=my_model)
    bot = host.build("ledger-invariant-checker")
    reply = await bot.respond(caller, AgentInput(message="Check the ledger.", today="2026-11-09"))

No FastAPI, Postgres or Redis is needed. The host is the only place that wires a bot to a tool
backend and a model, so the same bot runs in-process, behind the HTTP service, or from the CLI.
"""

from uaap.bots.catalog import BOTS, BotSpec, all_specs, get_spec
from uaap.bots.host import BotHost

__all__ = ["BOTS", "BotHost", "BotSpec", "all_specs", "get_spec"]
