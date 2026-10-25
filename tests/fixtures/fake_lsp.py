"""A tiny stdlib LSP server for hermetic tests — no real language server required.

Speaks just enough of the protocol for `tests/test_lsp.py`: the initialize handshake
with fixed capabilities, canned answers for definition/references/hover/documentSymbol/
workspace.symbol, one published diagnostic per `didOpen`, and graceful shutdown/exit.

Invocation (see the test config table):

    python fake_lsp.py <message-log> <workspace-dir> [--no-references]

Every incoming message is appended to the message log as one JSON object per line, so a
test can assert what the client SENT (a `didChange` after an edit, one `didOpen`, …)
without exposing any client internals.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def read_message(stream):
    headers: dict[str, str] = {}
    while True:
        raw = stream.readline()
        if not raw:
            return None
        line = raw.decode("utf-8").strip()
        if not line:
            break
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    length = int(headers["content-length"])
    body = b""
    while len(body) < length:
        chunk = stream.read(length - len(body))
        if not chunk:
            return None
        body += chunk
    return json.loads(body.decode("utf-8"))


def write_message(message) -> None:
    body = json.dumps(message).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


LOG = Path(sys.argv[1]) if len(sys.argv) > 1 else None
WORKSPACE = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path.cwd()
NO_REFERENCES = "--no-references" in sys.argv[3:]


def log(method: str, params) -> None:
    if LOG is None:
        return
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"method": method, "params": params}) + "\n")


def publish(uri: str, severity: int, message: str, line: int, character: int) -> None:
    write_message(
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": uri,
                "diagnostics": [
                    {
                        "range": {
                            "start": {"line": line, "character": character},
                            "end": {"line": line, "character": character + 5},
                        },
                        "severity": severity,
                        "source": "fake-lsp",
                        "code": "E001",
                        "message": message,
                    }
                ],
            },
        }
    )


def respond(request_id, result) -> None:
    write_message({"jsonrpc": "2.0", "id": request_id, "result": result})


def handle_request(request_id, method: str, params) -> None:
    if method == "initialize":
        # The wire shape the LSP spec defines: these live at the TOP level of
        # ServerCapabilities (`workspace.symbolProvider` is the nested exception). The
        # fixture used to mirror the client's (wrong) lookup path, which is how a
        # capability check that refused every real server passed its tests.
        capabilities = {
            "definitionProvider": True,
            "hoverProvider": True,
            "documentSymbolProvider": True,
            "workspaceSymbolProvider": True,
        }
        if not NO_REFERENCES:
            capabilities["referencesProvider"] = True
        respond(
            request_id,
            {
                "capabilities": capabilities,
                "serverInfo": {"name": "fake-lsp", "version": "0"},
            },
        )
        return
    if method == "shutdown":
        respond(request_id, None)
        return
    if method == "textDocument/definition":
        uri = (params.get("textDocument") or {}).get("uri", "")
        respond(
            request_id,
            {
                "uri": uri,
                "range": {
                    # Line 1 (0-based) exists in the test file, so the client can attach a
                    # snippet — the point of this canned answer.
                    "start": {"line": 1, "character": 4},
                    "end": {"line": 1, "character": 10},
                },
            },
        )
        return
    if method == "textDocument/references":
        uri = (params.get("textDocument") or {}).get("uri", "")
        respond(
            request_id,
            [
                {
                    "uri": uri,
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 3},
                    },
                },
                {
                    "uri": uri,
                    "range": {
                        "start": {"line": 4, "character": 2},
                        "end": {"line": 4, "character": 8},
                    },
                },
            ],
        )
        return
    if method == "textDocument/hover":
        respond(request_id, {"contents": {"kind": "markdown", "value": "**canned hover**"}})
        return
    if method == "textDocument/documentSymbol":
        respond(
            request_id,
            [
                {
                    "name": "canned_function",
                    "kind": 12,
                    "detail": "def canned_function() -> int",
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 1, "character": 0},
                    },
                    "selectionRange": {
                        "start": {"line": 0, "character": 4},
                        "end": {"line": 0, "character": 19},
                    },
                    "children": [
                        {
                            "name": "canned_child",
                            "kind": 13,
                            "range": {
                                "start": {"line": 1, "character": 4},
                                "end": {"line": 1, "character": 20},
                            },
                            "selectionRange": {
                                "start": {"line": 1, "character": 4},
                                "end": {"line": 1, "character": 16},
                            },
                        }
                    ],
                }
            ],
        )
        return
    if method == "workspace/symbol":
        query = str(params.get("query") or "")
        respond(
            request_id,
            [
                {
                    "name": f"canned_{query}",
                    "kind": 12,
                    "containerName": "canned_module",
                    "location": {
                        "uri": (WORKSPACE / "canned_symbols.py").as_uri(),
                        "range": {
                            "start": {"line": 2, "character": 0},
                            "end": {"line": 2, "character": 10},
                        },
                    },
                }
            ],
        )
        return
    write_message(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    )


def handle_notification(method: str, params) -> None:
    if method == "textDocument/didOpen":
        uri = (params.get("textDocument") or {}).get("uri", "")
        publish(uri, 1, "canned error", 2, 4)
        return
    if method == "textDocument/didChange":
        # A real server re-analyzes on change; the message text differs so a test can
        # tell a FRESH answer from the pre-change snapshot.
        uri = (params.get("textDocument") or {}).get("uri", "")
        publish(uri, 1, "canned error after change", 2, 4)
        return
    if method == "exit":
        raise SystemExit(0)


def main() -> None:
    while True:
        message = read_message(sys.stdin.buffer)
        if message is None:
            return
        method = str(message.get("method") or "")
        params = message.get("params") or {}
        log(method, params)
        if "id" in message:
            handle_request(message.get("id"), method, params)
        else:
            handle_notification(method, params)


if __name__ == "__main__":
    main()
