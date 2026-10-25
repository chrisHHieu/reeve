"""Compaction must not outlive the secrets it saw.

The compacted block rides every later request and persists with the session — a
credential that slips into the summary gains a longer life than the tool result it came
from. The summarizer prompt forbids quoting secrets; `redact_secrets` is the net that
makes the ask a guarantee, applied to every path into the block: the LLM summary, the
mechanically extracted state, and the verbatim user-message list.

The pattern set is deliberately high-precision: provider-key prefixes, labeled
assignments, JWTs, PEM blocks. Bare hex (git shas, session ids) stays — a redactor that
mangles prose trains nobody to trust it.
"""

from __future__ import annotations

import json

from harness.context.compaction import COMPACTION_INSTRUCTION, SUMMARY_SYSTEM_PROMPT, extract_user_messages, extract_working_state, redact_secrets, summarize_span

# Built at run time so no key-shaped literal sits in the source for a secret scanner to report.
OPENAI_SHAPED = "sk-proj-" + "Abcdefghijklmnopqrstuv1234"
PEM_HEAD, PEM_TAIL = "-----BEGIN RSA " + "PRIVATE KEY-----", "-----END RSA " + "PRIVATE KEY-----"


# -- the net --------------------------------------------------------------------


def test_provider_key_shapes_are_redacted():
    text = (
        f"set OPENAI key {OPENAI_SHAPED} and "
        "GITHUB ghp_" + "a" * 36 + " plus AWS AKIAIOSFODNN7EXAMPLE"
    )
    out = redact_secrets(text)
    assert "sk-proj-" not in out
    assert "ghp_" not in out
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert out.count("[REDACTED]") == 3


def test_labels_survive_values_do_not():
    out = redact_secrets("API_KEY=supersecretvalue123 and password: hunter2hunter2")
    assert "supersecretvalue123" not in out
    assert "hunter2hunter2" not in out
    assert "API_KEY=[REDACTED]" in out
    assert "password: [REDACTED]" in out


def test_bearer_header_keeps_its_label():
    out = redact_secrets("curl -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5.eyJzdWIiOiIxMjM0NTY3.PlainSig'")
    assert "Bearer [REDACTED]" in out
    assert "eyJhbGciOiJIUzI1NiIsInR5" not in out


def test_a_pem_block_vanishes_whole():
    pem = f"{PEM_HEAD}\nMIIEowIBAAK\n...\n{PEM_TAIL}"
    out = redact_secrets(f"saved key\n{pem}\ndone")
    assert "MIIEowIBAAK" not in out
    assert "[REDACTED]" in out


def test_prose_is_not_mangled():
    text = (
        "The sha 9a8b7c6d5e is fine, run git push, budget 250000 tokens, "
        "see docs/auth.md for the login flow, password policy updated."
    )
    assert redact_secrets(text) == text


def test_redaction_is_idempotent():
    once = redact_secrets(f"key {OPENAI_SHAPED}")
    twice = redact_secrets(once)
    assert once == twice


# -- the choke points -------------------------------------------------------------


def test_summarizer_output_is_redacted():
    class FakeSummarizer:
        def complete(self, *, model, messages, tools=None, **settings):
            class Turn:
                text = f"User set the key {OPENAI_SHAPED} last week."

            return Turn()

    out = summarize_span(FakeSummarizer(), "m", [])
    assert "sk-proj-" not in out and "[REDACTED]" in out


def test_working_state_redacts_shell_commands():
    span = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "run_shell",
                        "arguments": json.dumps(
                            {"command": "curl -H 'Authorization: Bearer live-token-123456' https://api.example.com"}
                        ),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": '{"exit_code": 0}'},
    ]
    state = extract_working_state(span)
    assert "live-token-123456" not in state
    assert "api.example.com" in state  # the useful half stays


def test_user_messages_are_redacted_but_intent_stays():
    span = [
        {
            "role": "user",
            "content": f"Deploy with the staging key {OPENAI_SHAPED} please",
        }
    ]
    out = extract_user_messages(span)
    assert out and "sk-proj-" not in out[0]
    assert "Deploy with the staging key" in out[0]


def test_the_prompt_rule_rides_both_summarizer_shapes():
    assert "NEVER quote a secret" in SUMMARY_SYSTEM_PROMPT
    assert "NEVER quote a secret" in COMPACTION_INSTRUCTION
