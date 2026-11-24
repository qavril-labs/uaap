# UAAP portable bots

Six bots, each described by a manifest in `src/uaap/bots/catalog.py` and runnable on its own.

| Bot | Kind |
|---|---|
| universal-finance-agent | agent (model) |
| approval-agent | agent (model, inbox) |
| ledger-invariant-checker | deterministic |
| approval-reminder | deterministic (inbox) |
| onboard-ola | agent (model) |
| ticket-tess | agent (model) |

## Use anywhere

**In code (no service, database or Redis):**

```python
from uaap.bots import BotHost
host = BotHost(tools=register_my_tools, model=my_model)  # model only for agent bots
bot = host.build("ledger-invariant-checker")
reply = await bot.respond(caller, AgentInput(message="Check the ledger.", today="2026-11-09"))
```

**CLI:** `python -m uaap.bots list`, `describe <bot-id>`, and for deterministic bots
`run <bot-id> <message>` with `CORE_SAAS_URL`, `CORE_SAAS_TOKEN` (and optional `CORE_SAAS_COMPANY_ID`).

**MCP tool server (stdio, no extra dependency):** `python -m uaap.bots mcp` exposes the deterministic bots as MCP tools; same environment variables as `run`.

**HTTP service:** unchanged (`uaap.api.main`, `Dockerfile`), all six bots served under `/v1/agents`.

Adding a bot: write the agent, add one `BotSpec`; `tests/test_bots_host.py` checks it matches its card.
