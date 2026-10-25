"""Trying the messaging connectors by hand, without the app.

    python -m harness.integrations.connectors.cli status
        Each platform: whether it is on, and who it lets in.

    python -m harness.integrations.connectors.cli fake [--user U1] [--allow U1]
        Type as if a platform delivered your words; an echo handler answers through the gateway.
        Authorization, delivery in and delivery out, all offline. A --user who is not one of the
        --allow ids shows a message being dropped.

    python -m harness.integrations.connectors.cli send --target telegram:12345 --text "hi"
        A real send through the send_message tool; the bot's token must be in the secret store.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from harness.safety.secrets import SecretStore

from .config import ConnectorSettings, load_settings
from .messaging import FakeAdapter, MessageEvent
from .messaging.gateway import Gateway
from .outbound import make_send_message_tool


def _status() -> int:
    print("Connector status:")
    for platform, settings in load_settings(SecretStore()).items():
        who = f"allow_all={settings.allow_all}  allowed_users={len(settings.allowed_users)}"
        print(f"  {platform:10s} enabled={settings.enabled}  {who}")
    return 0


async def _fake(user: str, allow: list[str]) -> int:
    adapter = FakeAdapter()
    open_to_all = not allow
    gateway = Gateway(settings={"fake": ConnectorSettings(platform="fake", enabled=True, allowed_users=set(allow), allow_all=open_to_all)})

    async def echo(event: MessageEvent) -> None:
        answer = f"echo: {event.text}"
        await gateway.deliver(event.source.target, answer)
        print(f"  ↩ sent to {event.source.target}: {answer!r}")

    gateway.set_handler(echo)
    gateway.register(adapter)
    await gateway.start()
    print(f"fake gateway up (user={user}, allow={'∗ all' if open_to_all else allow}). Ctrl-D to quit.\n")
    try:
        while True:
            try:
                text = (await asyncio.to_thread(input, "you> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if text:
                answered = len(adapter.outbox)
                await adapter.inject(text, user_id=user, user_name=user)
                if len(adapter.outbox) == answered:
                    print("  ⨯ dropped (not authorized)")
    finally:
        await gateway.stop()


def _send(target: str, text: str) -> int:
    result = make_send_message_tool(SecretStore())(target=target, text=text)
    print(result)
    return 0 if result.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reeve-connectors")
    commands = parser.add_subparsers(dest="cmd", required=True)
    commands.add_parser("status")
    fake = commands.add_parser("fake")
    fake.add_argument("--user", default="u1")
    fake.add_argument("--allow", action="append", default=[], help="authorized user id (repeatable)")
    send = commands.add_parser("send")
    send.add_argument("--target", required=True)
    send.add_argument("--text", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "status":
        return _status()
    if args.cmd == "fake":
        return asyncio.run(_fake(args.user, args.allow))
    return _send(args.target, args.text)


if __name__ == "__main__":
    sys.exit(main())
