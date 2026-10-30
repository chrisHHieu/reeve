"""Anthropic's cache diagnostics, threaded through a conversation.

`_Api` stands in for the Messages API: it records each request and answers with an id and the
`diagnostics` value the test scripts. What is pinned: each request of a conversation names the
previous response of THAT conversation, the reading lands on the turn as the event log keeps it,
the field goes only to Anthropic's own endpoint, and a 400 about it turns it off without costing
the turn.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from test_harness_review import harness_review

from harness.llm.providers import AnthropicProvider
from harness.llm.providers.turns import CACHE_AFFINITY

MODEL = "claude-sonnet-5-5"
MESSAGES = [{"role": "user", "content": "hello"}]


class BadRequestError(Exception):
    """Named like the SDK's 400, which is all the provider reads of it."""


def _usage():
    return SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)


class _Api:
    def __init__(self, answers=(), refuse_field=False):
        self.requests: list[dict] = []
        self.answers = list(answers)
        self.refuse_field = refuse_field

        def create(**request):
            self.requests.append(request)
            if self.refuse_field and "diagnostics" in (request.get("extra_body") or {}):
                raise BadRequestError("Error code: 400 - diagnostics: Extra inputs are not permitted")
            diagnostics = self.answers.pop(0) if self.answers else None
            reply_id = f"msg_{len(self.requests)}"
            if request.get("stream"):
                return iter([
                    SimpleNamespace(type="message_start", message=SimpleNamespace(id=reply_id, usage=_usage(), diagnostics=diagnostics)),
                    SimpleNamespace(type="content_block_start", index=0, content_block=SimpleNamespace(type="text", text="")),
                    SimpleNamespace(type="content_block_delta", index=0, delta=SimpleNamespace(type="text_delta", text="ok")),
                    SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="end_turn"), usage=SimpleNamespace(output_tokens=5)),
                ])
            return SimpleNamespace(
                id=reply_id, content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn",
                usage=_usage(), diagnostics=diagnostics,
            )

        self.messages = SimpleNamespace(create=create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))

    def asked(self, index):
        return (self.requests[index].get("extra_body") or {}).get("diagnostics")


def _native(api, **options):
    """The provider as it is built for Anthropic's own endpoint, with the SDK client swapped."""
    provider = AnthropicProvider(api_key="sk-test", **options)
    provider._client = api
    return provider


def _changed(kind, tokens):
    return {"cache_miss_reason": {"type": kind, "cache_missed_input_tokens": tokens}}


def test_each_request_names_the_previous_response_of_its_conversation():
    api = _Api(answers=[None, None, _changed("messages_changed", 41_850)])
    provider = _native(api)
    first = provider.complete(model=MODEL, messages=MESSAGES, **{CACHE_AFFINITY: "session-a"})
    second = provider.complete(model=MODEL, messages=MESSAGES, **{CACHE_AFFINITY: "session-a"})
    third = provider.complete(model=MODEL, messages=MESSAGES, **{CACHE_AFFINITY: "session-a"})
    assert [api.asked(i) for i in range(3)] == [
        {"previous_message_id": None}, {"previous_message_id": "msg_1"}, {"previous_message_id": "msg_2"},
    ]
    assert first.cache_miss is None  # the opt-in turn compares with nothing
    assert second.cache_miss == {"type": "unchanged"}  # compared, and nothing diverged
    assert third.cache_miss == {"type": "messages_changed", "tokens": 41_850}


def test_two_conversations_never_compare_with_each_other_and_a_pass_with_no_session_asks_nothing():
    api = _Api()
    provider = _native(api)
    provider.complete(model=MODEL, messages=MESSAGES, **{CACHE_AFFINITY: "a"})
    provider.complete(model=MODEL, messages=MESSAGES, **{CACHE_AFFINITY: "b"})
    provider.complete(model=MODEL, messages=MESSAGES, **{CACHE_AFFINITY: "a"})
    turn = provider.complete(model=MODEL, messages=MESSAGES)
    assert [api.asked(i) for i in range(4)] == [
        {"previous_message_id": None}, {"previous_message_id": None}, {"previous_message_id": "msg_1"}, None,
    ]
    assert turn.cache_miss is None


def test_a_streamed_reply_carries_its_id_and_reading_from_message_start():
    api = _Api(answers=[None, _changed("system_changed", 9_000)])
    provider = _native(api)
    for _ in range(2):
        chunks = list(provider.stream(model=MODEL, messages=MESSAGES, **{CACHE_AFFINITY: "s"}))
    assert api.asked(1) == {"previous_message_id": "msg_1"}
    assert chunks[-1].turn.cache_miss == {"type": "system_changed", "tokens": 9_000}


def test_a_comparison_still_running_is_kept_as_pending():
    api = _Api(answers=[None, {"cache_miss_reason": None}])
    provider = _native(api)
    provider.complete(model=MODEL, messages=MESSAGES, **{CACHE_AFFINITY: "s"})
    assert provider.complete(model=MODEL, messages=MESSAGES, **{CACHE_AFFINITY: "s"}).cache_miss == {"type": "pending"}


def test_only_anthropics_own_endpoint_is_asked():
    gateway = _Api()
    AnthropicProvider(client=gateway).complete(model=MODEL, messages=MESSAGES, **{CACHE_AFFINITY: "s"})  # a cloud, a gateway
    proxy = _Api()
    _native(proxy, base_url="https://llm-proxy.example.com").complete(model=MODEL, messages=MESSAGES, **{CACHE_AFFINITY: "s"})
    assert gateway.asked(0) is None and proxy.asked(0) is None


def test_a_400_about_the_field_turns_it_off_and_the_turn_goes_on():
    api = _Api(refuse_field=True)
    provider = _native(api)
    turn = provider.complete(model=MODEL, messages=MESSAGES, **{CACHE_AFFINITY: "s"})
    provider.complete(model=MODEL, messages=MESSAGES, **{CACHE_AFFINITY: "s"})
    assert turn.text == "ok" and turn.cache_miss is None
    assert [api.asked(i) is not None for i in range(len(api.requests))] == [True, False, False]


def test_the_review_tells_a_prefix_the_harness_changed_from_one_the_provider_dropped(tmp_path):
    def step(read, told=None):
        row = {"type": "assistant/message", "t": 5.0, "model": "anthropic-subscription:" + MODEL,
               "usage": {"cache_read": read, "input": 50}}
        if told:
            row["cache_miss"] = {"type": told}
        return row

    path = tmp_path / "s.events.jsonl"
    rows = [
        step(60_000), step(1_000, "messages_changed"),   # the harness rewrote it without a mark
        step(60_000), step(2_000, "unchanged"),          # the request matched: the provider's
        step(60_000), step(3_000),                       # no reading: still unexplained
    ]
    path.write_text("\n".join(json.dumps({"seq": i, **r}) for i, r in enumerate(rows)), encoding="utf-8")
    counts, refs = harness_review.review([path], keep_all="unmarked-prefix-changes")
    assert counts["cache-drops api messages_changed"] == 1
    assert counts["cache-drops provider dropped"] == 1 and counts["cache-drops unexplained"] == 1
    assert counts["unmarked-prefix-changes"] == 1 and refs["unmarked-prefix-changes"][0][2] == "messages_changed"
