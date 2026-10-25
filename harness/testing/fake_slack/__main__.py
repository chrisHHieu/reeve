"""Run FakeSlack on its own, to point a dev server at it.

    python -m harness.testing.fake_slack [--host 127.0.0.1] [--port 8910]

Export the `SLACK_API_URL` it prints before starting `reeve-server`, connect Slack in the app with
any `xoxb-` and `xapp-` tokens, and play a scenario with the curl lines it prints.
"""

from __future__ import annotations

import argparse

import uvicorn

from .server import FakeSlack

_JSON = "-H 'content-type: application/json'"


def _how_to_use(base: str) -> str:
    control = f"{base}/control"
    examples = [
        f"curl -X POST {control}/users {_JSON} -d '{{\"id\":\"U1\",\"name\":\"alice\",\"real_name\":\"Alice\"}}'",
        f"curl -X POST {control}/channels {_JSON} -d '{{\"id\":\"C1\",\"name\":\"general\",\"is_im\":false}}'",
        f"curl -X POST {control}/inbound {_JSON} -d '{{\"channel\":\"C1\",\"user\":\"U1\",\"text\":\"hello there\"}}'",
        f"curl -X POST {control}/interaction {_JSON} -d '{{\"channel\":\"C1\",\"user\":\"U1\",\"username\":\"alice\","
        f"\"message_ts\":\"1700000001.000001\",\"action_id\":\"rv_0\",\"value\":\"...\"}}'",
        f"curl {control}/outbound        # what the app posted or edited",
        f"curl -X POST {control}/reset   # start clean",
        f"curl {control}/health",
    ]
    return "\n".join(
        [
            f"FakeSlack on {base}",
            "",
            "Before starting reeve-server:",
            f"  export SLACK_API_URL={base}/api/",
            "",
            "Scenario verbs:",
            *(f"  {line}" for line in examples),
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="harness.testing.fake_slack", description="A local Slack for the dev app.")
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (127.0.0.1)")
    parser.add_argument("--port", type=int, default=8910, help="port to listen on (8910)")
    args = parser.parse_args()
    fake = FakeSlack(host=args.host, port=args.port)
    print(_how_to_use(f"http://{args.host}:{args.port}"))
    # A fixed port, unlike in tests: the socket address the Web API hands out is known before the
    # bind. uvicorn.run blocks and handles Ctrl+C.
    uvicorn.run(fake.app, host=args.host, port=args.port, log_level="info", lifespan="off")


if __name__ == "__main__":
    main()
