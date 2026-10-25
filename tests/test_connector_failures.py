"""Item 4.4 — a connector failure that names the next move.

A tool result has two readers at once: the model, choosing what to do next, and the person
watching the result card. `channel_not_found` serves neither — the model retries the same
call because nothing told it not to, and the person gets a word from a language they do not
speak attached to a thing that did not happen.

The rule these tests defend is as much about restraint as about coverage: an unrecognized
error is passed through UNCHANGED. Inventing a next step for a failure we do not recognize
is worse than the bare code, because people follow instructions.
"""

from __future__ import annotations

from harness.integrations.connectors.messaging import SendResult
from harness.integrations.connectors.failures import actionable
from harness.integrations.connectors.outbound import make_send_message_tool
from harness.safety.secrets import SecretStore


# -- the mapper -------------------------------------------------------------------


def test_slack_codes_become_something_to_do():
    assert "invite it in Slack" in actionable("not_in_channel", platform="slack")
    assert "reconnect Slack" in actionable("invalid_auth", platform="slack")
    assert "unarchive" in actionable("is_archived", platform="slack")
    assert "shorter parts" in actionable("msg_too_long", platform="slack")
    # The one sentence that already existed, now reachable from every sender.
    assert actionable("not_in_channel", platform="slack") != "not_in_channel"


def test_a_slack_code_with_detail_appended_still_matches():
    note = actionable("missing_scope: chat:write", platform="slack")
    assert "missing a permission" in note


def test_telegram_prose_is_matched_on_its_fragment():
    assert "message the bot once" in actionable(
        "Bad Request: chat not found", platform="telegram"
    )
    assert "unblock" in actionable(
        "Forbidden: bot was blocked by the user", platform="telegram"
    )


def test_a_transport_failure_says_so_on_any_platform():
    for text in ("[Errno 11001] getaddrinfo failed", "ConnectTimeout", "read timed out"):
        note = actionable(text, platform="telegram")
        assert "couldn't reach Telegram" in note, text
    assert "couldn't reach the service" in actionable("ConnectError", platform="")


def test_an_unrecognized_error_is_returned_untouched():
    """The restraint half. No invented instruction, on either axis: an unknown code, and
    a known code on the WRONG platform (Telegram never returns `not_in_channel`)."""
    assert actionable("some_new_code_2027", platform="slack") == "some_new_code_2027"
    assert actionable("not_in_channel", platform="telegram") == "not_in_channel"
    assert actionable("", platform="slack") == ""


def test_a_slack_code_is_not_matched_as_a_substring():
    """`channel_not_found` must not swallow a longer, different code that contains it."""
    assert actionable("x_channel_not_found", platform="slack") == "x_channel_not_found"


# -- and it reaches the tool result, which is the point ----------------------------


def _tool(tmp_path, result: SendResult):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("telegram:default", {"type": "token", "bot_token": "T0K"})
    return make_send_message_tool(
        secrets, senders={"telegram": lambda *a, **k: result}
    )


def test_the_send_tool_hands_the_next_move_to_both_readers(tmp_path):
    tool = _tool(tmp_path, SendResult(False, error="Bad Request: chat not found"))
    error = tool(target="telegram:12345", text="hi")["error"]
    assert "message the bot once" in error
    assert "Bad Request" not in error


def test_the_send_tool_still_passes_an_unknown_failure_through(tmp_path):
    tool = _tool(tmp_path, SendResult(False, error="teapot_error"))
    assert tool(target="telegram:12345", text="hi")["error"] == "teapot_error"
