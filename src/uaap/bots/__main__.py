"""`python -m uaap.bots list` and `python -m uaap.bots describe <bot-id>`."""

from __future__ import annotations

import asyncio
import os
import sys

from uaap.bots.catalog import all_specs, get_spec
from uaap.errors import UaapError


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "list":
        for b in all_specs():
            print(f"{b.id:28} {b.kind:14} v{b.version}  {b.summary}")
        return 0
    if argv[0] == "describe" and len(argv) == 2:
        try:
            spec = get_spec(argv[1])
        except UaapError as exc:
            print(exc, file=sys.stderr)
            return 1
        card = spec.load().card
        print(f"{spec.name} ({spec.id}) v{spec.version}, {spec.kind}")
        print(f"needs model: {spec.needs_model}; needs inbox: {spec.needs_inbox}")
        print(f"purpose: {card.purpose}")
        return 0
    if argv[0] == "run" and len(argv) >= 3:
        from uaap.bots.run import run_bot

        spec = get_spec(argv[1])
        if spec.needs_model:
            print(f"'{spec.id}' needs a model; run it from code with BotHost(model=...)", file=sys.stderr)
            return 1
        core_url, token = os.environ.get("CORE_SAAS_URL"), os.environ.get("CORE_SAAS_TOKEN")
        if not core_url or not token:
            print("Set CORE_SAAS_URL and CORE_SAAS_TOKEN", file=sys.stderr)
            return 1
        company = os.environ.get("CORE_SAAS_COMPANY_ID")
        message = " ".join(argv[2:])
        reply = asyncio.run(run_bot(spec.id, message, core_url=core_url, token=token, company_id=company))
        print(f"[{reply.status}] {reply.text}")
        return 0 if reply.status == "answered" else 1
    if argv[0] == "mcp":
        from uaap.bots.mcp_server import serve

        ids = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--bot"]
        return asyncio.run(serve(ids or None))
    usage = "usage: python -m uaap.bots [list | describe <bot-id> | run <bot-id> <message> | mcp]"
    print(usage, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
