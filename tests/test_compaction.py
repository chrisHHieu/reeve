"""Compaction's pure parts: when it is due, where a cut falls, what is kept word for word
from the cut span, how the summary is asked for, and the view a provider is sent after it.
No engine is involved.
"""

import json
from itertools import count

import pytest

from summary_fixtures import valid_summary

from harness.context.compaction import COMPACTION_INSTRUCTION, CompactionState, DEFAULT_BUFFER_TOKENS, DEFAULT_CONTEXT_WINDOW, IMAGE_OFFLOAD_PLACEHOLDER, IMAGE_TOKEN_ESTIMATE, PDF_TOKEN_ESTIMATE, apply_to_outbound, build_state, capped_keep_tokens, compacted_block, compaction_instruction, estimate_tokens, extract_user_messages, extract_working_state, is_context_overflow, normalize_plan, offload_images, pick_boundary, prune_keep_tokens, render_standing_plan, replace_offloaded_images, should_compact, summarize_span, summarizer_messages, trigger_tokens

_call_ids = count()


def user(text):
    return {"role": "user", "content": text, "ts": 1.0}


def assistant(text="", tool_calls=None):
    message = {"role": "assistant", "content": text, "ts": 1.0}
    if tool_calls:
        message["tool_calls"] = [{"id": f"c{next(_call_ids)}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}} for name, args in tool_calls]
    return message


def tool(call_id, content):
    return {"role": "tool", "tool_call_id": call_id, "content": content if isinstance(content, str) else json.dumps(content), "ts": 1.0}


def tool_turn(name, args, result):
    """A call and its result, paired by id."""
    call = assistant(tool_calls=[(name, args)])
    return [call, tool(call["tool_calls"][0]["id"], result)]


def convo(turns=6, bulk=2000):
    """A system prompt, then `turns` requests each answered at length."""
    messages = [{"role": "system", "content": "You are a coding agent."}]
    for i in range(turns):
        messages += [user(f"request {i}"), assistant(f"answer {i} " + "x" * bulk)]
    return messages


class FakeSummarizer:
    """Answers a summary request with `text` (a valid eight-section summary by default, since a
    missing section is asked for once more and then refused) after failing `fail_times` times."""

    def __init__(self, text=None, fail_times=0):
        self.text = text if text is not None else valid_summary("all good")
        self.fail_times = fail_times
        self.calls = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls.append(dict(settings, model=model, messages=messages, tools=tools))
        if self.fail_times:
            self.fail_times = self.fail_times - 1
            raise RuntimeError("the summary model is unavailable")
        return type("Turn", (), {"text": self.text})()


def _keep(messages, last):
    return estimate_tokens(messages[-last:]) + 10


# -- when it is set off --------------------------------------------------------------------------
#
# The rule: compact when the window is within `max(output, buffer)` of full. There
# is no threshold fraction and no absolute cap — the old pct/cap pair is gone.


def test_trigger_is_the_window_minus_the_reserve():
    assert trigger_tokens(100_000) == 80_000  # 100k − 20k buffer
    assert trigger_tokens(1_000_000) == 980_000  # no cap: the 1M window is used
    assert trigger_tokens(None) == DEFAULT_CONTEXT_WINDOW - DEFAULT_BUFFER_TOKENS
    # The buffer is user-overridable, and a bigger output budget reserves more than it.
    assert trigger_tokens(100_000, buffer_tokens=5_000) == 95_000
    assert trigger_tokens(100_000, output_tokens=50_000) == 50_000
    # A small output never reserves LESS than the buffer (max, not min).
    assert trigger_tokens(100_000, output_tokens=8_000) == 80_000


def test_compaction_is_due_from_the_trigger_on():
    assert not should_compact(79_999, 100_000) and should_compact(80_000, 100_000)


def test_the_trigger_reserves_room_for_the_next_answer():
    """`max(output, buffer)`: on a 32k window with an 8k answer the 20k buffer wins, so the
    trigger is 12.8k — comfortably before the window fills. A huge output reserves that."""
    assert trigger_tokens(32_768, output_tokens=8_000) == 12_768
    assert trigger_tokens(32_768) == 12_768  # buffer is the floor either way
    assert trigger_tokens(1_000_000, output_tokens=200_000) == 800_000
    assert should_compact(12_768, 32_768, output_tokens=8_000)
    assert not should_compact(12_767, 32_768, output_tokens=8_000)


def test_the_answer_reserve_is_capped_at_half_the_window():
    """A catalogue can state `output_length >= context_length` (models.dev lists grok-4.6
    at 500k/500k). `window - output` then lands on 1 and every step is "due to compact".
    The reserve can never be a real answer budget past half the window, so it clamps there
    while every model whose output fits inside its window keeps its exact numbers."""
    assert trigger_tokens(500_000, output_tokens=500_000) == 250_000
    assert trigger_tokens(100_000, output_tokens=100_000) == 50_000
    assert trigger_tokens(100_000, output_tokens=60_000) == 50_000
    # Just under half still reserves exactly what the catalogue says.
    assert trigger_tokens(100_000, output_tokens=49_000) == 51_000
    # Junk metadata (negative, zero) cannot move the trigger past the buffer floor.
    assert trigger_tokens(100_000, output_tokens=-5) == 80_000
    assert trigger_tokens(0) == DEFAULT_CONTEXT_WINDOW - DEFAULT_BUFFER_TOKENS


def test_a_capped_window_scales_the_verbatim_keep():
    """The fixed 8k default is sized for uncapped 600k+ windows; keeping 8k of a 50k cap
    made a real run redo its work (measured 2.15x cost). A quarter of the capped window,
    ceiling 40k, and a larger configured keep still wins."""
    assert capped_keep_tokens(8_000, 50_000) == 12_500
    assert capped_keep_tokens(8_000, 150_000) == 37_500
    assert capped_keep_tokens(8_000, 250_000) == 40_000  # the ceiling
    assert capped_keep_tokens(60_000, 150_000) == 60_000  # configured wins
    assert capped_keep_tokens(0, 32_000) == 8_000


def test_the_prune_protect_band_has_an_absolute_floor():
    """Recent tool output is protected: stays whatever the window size, so a
    small window cannot fold the working set out from under the agent."""
    assert prune_keep_tokens(250_000) == 40_000  # the absolute floor wins
    assert prune_keep_tokens(1_000_000) == 100_000  # fraction wins on a huge trigger
    assert prune_keep_tokens(30_000) == 30_000  # never protects more than the trigger


# -- image offload -------------------------------------------------------------


def _image_message(text="look at this"):
    return {"role": "user", "content": [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}


def test_image_offload_keeps_the_newest_and_is_monotonic():
    msgs = [{"role": "system", "content": "sys"}, _image_message("one"), {"role": "assistant", "content": "ok"}, _image_message("two"), _image_message("three")]
    assert offload_images(msgs) == [1]  # newest two kept, index 1 offloaded
    # The prior decision carries forward even when a newer image arrives later: index 3
    # joins the offloaded set, index 1 does not come back.
    assert offload_images([*msgs, _image_message("four")], prior=[1]) == [1, 3]


def test_replace_offloaded_images_swaps_parts_only_where_indexed():
    msgs = [{"role": "system", "content": "sys"}, _image_message("one"), _image_message("two")]
    out = replace_offloaded_images(msgs, [1])
    assert out[1]["content"][1]["type"] == "text" and IMAGE_OFFLOAD_PLACEHOLDER in out[1]["content"][1]["text"]
    assert out[2]["content"][1]["type"] == "image_url"  # untouched
    assert msgs[1]["content"][1]["type"] == "image_url"  # canonical history untouched


def test_apply_to_outbound_honours_offloaded_images():
    msgs = [{"role": "system", "content": "sys"}, _image_message("one"), {"role": "assistant", "content": "ok"}, {"role": "user", "content": "next"}]
    state = CompactionState(boundary_index=0, summary_text="", working_state="", offloaded_images=[1])
    assert apply_to_outbound(msgs, state)[1]["content"][1]["type"] == "text"


# -- custom summarizer instructions --------------------------------------------


def test_custom_instructions_replace_the_summary_prompt():
    span = [user("hello"), assistant("hi")]
    assert "Primary request and intent" in summarizer_messages(span)[0]["content"]
    assert summarizer_messages(span, instructions="Keep only ticket ids and owners.")[0]["content"] == "Keep only ticket ids and owners."
    # The replay shape uses the custom text too, with the deixis note; empty falls back.
    assert "Keep only ticket ids" in compaction_instruction("Keep only ticket ids.")
    assert compaction_instruction("") == COMPACTION_INSTRUCTION


def test_summarize_span_uses_custom_instructions_on_both_shapes():
    span = [user("hello there"), assistant("hi")]
    plain = FakeSummarizer()
    summarize_span(plain, "m", span, instructions="Only the facts.")
    assert plain.calls[0]["messages"][0]["content"] == "Only the facts."
    replay = FakeSummarizer()
    summarize_span(replay, "m", span, replay=[user("old")], instructions="Only the facts.")
    assert "Only the facts." in replay.calls[0]["messages"][-1]["content"]


# -- the token estimate --------------------------------------------------------


def test_the_estimate_is_about_a_token_per_four_characters():
    assert 100 <= estimate_tokens([user("a" * 400)]) <= 120  # 400 characters and the JSON around them


def test_estimate_tokens_measures_the_text_not_its_ascii_escape():
    """`json.dumps` escapes non-ASCII to `\\uXXXX` by default, which measured a
    33-character Vietnamese sentence as 80 "characters" — so a session written in
    Vietnamese estimated its window at roughly twice its real size. The bias correction
    cannot absorb that reliably (it is one ratio for the whole session, and a session
    mixing an English system prompt with a Vietnamese conversation fits neither) and the
    uncalibrated path applies no ratio at all, which is the state right after every
    compaction."""
    vietnamese = [user("Hồ sơ thuế phải nộp trước thứ Sáu")]
    ascii_only = [user("a" * 33)]
    assert len(vietnamese[0]["content"]) == len(ascii_only[0]["content"])
    assert estimate_tokens(vietnamese) == estimate_tokens(ascii_only)


def test_estimate_tokens_prices_a_screenshot_at_pixels_not_base64_bytes():
    """Attachments are data URLs: their character count is file size, while what a
    provider bills is pixels. Left in the char count, one screenshot measured at a
    quarter-million "tokens" against a 128k window — the trigger fired on a context
    that was mostly empty, and the same image re-fired it after every compaction."""
    png = "data:image/png;base64," + "A" * 1_000_000
    parts = [{"type": "text", "text": "what is wrong here?"}, {"type": "image_url", "image_url": {"url": png}}]
    assert IMAGE_TOKEN_ESTIMATE <= estimate_tokens([{"role": "user", "content": parts}]) < 5_000  # not ~250_000
    # A remote URL has no inlined payload to blank, and stays cheap on its own.
    remote = [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}]
    assert estimate_tokens([{"role": "user", "content": remote}]) < 100


def test_estimate_tokens_prices_inlined_documents_too():
    pdf = "data:application/pdf;base64," + "A" * 4_000_000
    parts = [{"type": "text", "text": "summarize"}, {"type": "file", "file": {"filename": "a.pdf", "file_data": pdf}}]
    assert PDF_TOKEN_ESTIMATE <= estimate_tokens([{"role": "user", "content": parts}]) < PDF_TOKEN_ESTIMATE + 100


def test_estimate_tokens_counts_each_attachment():
    png = "data:image/png;base64," + "A" * 100_000
    one = estimate_tokens([user([{"type": "image_url", "image_url": {"url": png}}])])
    three = estimate_tokens([user([{"type": "image_url", "image_url": {"url": png}} for _ in range(3)])])
    # Two more attachments: the fixed price twice, plus the tiny JSON envelope each
    # blanked part still contributes.
    assert one + 2 * IMAGE_TOKEN_ESTIMATE <= three < one + 2 * IMAGE_TOKEN_ESTIMATE + 200


def test_the_attachment_price_never_mutates_history_or_text():
    png = "data:image/png;base64," + "A" * 500_000
    parts = [{"type": "image_url", "image_url": {"url": png}}]
    message = {"role": "user", "content": parts}
    estimate_tokens([message])
    assert message["content"] is parts  # the canonical list is untouched
    assert parts[0]["image_url"]["url"] == png  # and the payload is still whole
    # Text parts are measured exactly as before: chars/4 plus JSON overhead.
    assert 100 <= estimate_tokens([user("a" * 400)]) <= 120


def test_estimate_tokens_scales_with_real_characters_in_every_script():
    """Ten times the text must estimate ten times the tokens, whatever the script — the
    property the escaping broke, since a denser script produced a longer ESCAPE rather
    than a longer message. Sized so the fixed per-message JSON overhead is negligible."""
    for script, char in (("latin", "a"), ("vietnamese", "ồ"), ("cjk", "関")):
        short, long = estimate_tokens([user(char * 1_000)]), estimate_tokens([user(char * 10_000)])
        assert 9 * short <= long <= 11 * short, script


# -- where the cut falls -------------------------------------------------------------------------


def test_the_cut_falls_on_the_earliest_request_whose_rest_fits():
    messages = convo(turns=6)
    boundary = pick_boundary(messages, keep_tokens=estimate_tokens(messages[1:3]) * 2 + 10)
    assert messages[boundary]["role"] == "user" and messages[boundary]["content"] == "request 4"  # the last two turns stay


def test_a_turn_too_big_to_keep_is_cut_at_one_of_its_steps_never_at_a_result():
    messages = [{"role": "system", "content": "s"}, user("go")]
    for i in range(8):
        messages += tool_turn("run_shell", {"command": f"cmd{i}"}, {"exit_code": 0, "out": "z" * 3000})
        messages[-2]["content"] = "step " + "y" * 3000
    assert messages[pick_boundary(messages, keep_tokens=estimate_tokens(messages[-3:]))]["role"] == "assistant"


def test_nothing_is_cut_when_everything_fits():
    assert pick_boundary([{"role": "system", "content": "s"}, user("hi"), assistant("hello")], keep_tokens=10_000_000) is None


# -- what is kept from the cut span --------------------------------------------------------------


def test_the_kept_working_state_lists_files_newest_first_and_commands_with_their_exit():
    def wrote(path, body):
        return tool_turn("write_file", {"path": path, "content": body}, {"ok": True})

    block = extract_working_state([user("make the change"), *wrote("a.py", "x"), *tool_turn("run_shell", {"command": "pytest -q"}, {"exit_code": 1}), *wrote("b.py", "y"), *wrote("a.py", "x2")])
    assert block.index("- a.py") < block.index("- b.py") and block.count("a.py") == 1
    assert "pytest -q" in block and "[exit 1]" in block
    assert "run_shell" in block and "write_file" in block
    assert extract_working_state([user("hi"), assistant("yo")]) == ""


def test_the_persons_messages_are_kept_word_for_word_and_a_long_one_keeps_both_ends():
    kept = extract_user_messages([
        user("first ask"), assistant("a"),
        user([{"type": "text", "text": "second"}, {"type": "image_url", "image_url": {}}]), assistant("b"),
        user("bulk " + "z" * 2000 + " and do not touch the auth module"),
    ])
    assert kept[0] == "first ask" and kept[1] == "second [image]"
    # The instruction after a paste is on no disk to read back; the head alone used to be kept.
    assert len(kept[2]) <= 600 and kept[2].startswith("bulk zzz") and kept[2].endswith("and do not touch the auth module")
    assert "[…1,438 characters cut…]" in kept[2]


def test_user_messages_capped_across_repeated_compactions():
    # The mechanical user-message list must not grow forever — newest _USER_MESSAGES_MAX
    # survive, the rest stay counted so the block's "omitted" note is honest.
    from harness.context.compaction import _USER_MESSAGES_MAX
    from harness.context.compaction.extraction import cap_user_messages

    messages = [{"role": "system", "content": "s"}]
    for i in range(120):
        messages += [{"role": "user", "content": f"ask {i}"}, {"role": "assistant", "content": f"answer {i}"}]
    users, dropped = cap_user_messages(extract_user_messages(messages), prior_dropped=0, limit=_USER_MESSAGES_MAX)
    assert len(users) <= _USER_MESSAGES_MAX and dropped > 0 and users[-1].startswith("ask")
    state = CompactionState(boundary_index=1, summary_text="s", working_state="", user_messages=users, user_messages_dropped=dropped)
    assert f"{dropped} earlier user messages omitted" in compacted_block(state)
    restored = CompactionState.from_dict(state.as_dict())
    assert restored is not None and restored.user_messages_dropped == dropped and restored.user_messages == users


# -- asking for the summary ----------------------------------------------------------------------


def test_the_summary_request_cuts_long_results_and_carries_the_previous_summary():
    asked = summarizer_messages([user("go"), *tool_turn("read_file", {"path": "big.txt"}, "huge " * 500)], prior_summary="OLD SUMMARY")
    assert asked[0]["role"] == "system" and "Primary request and intent" in asked[0]["content"]
    assert "OLD SUMMARY" in asked[1]["content"] and len(asked[1]["content"]) < 3000  # the 2,500-character result was cut


def test_the_summary_is_asked_of_the_model_without_tools_and_an_empty_one_is_an_error_that_says_why():
    fake = FakeSummarizer(text="## ok")
    assert summarize_span(fake, "prov:model-x", [user("hi")]) == "## ok"
    assert fake.calls[0]["model"] == "prov:model-x" and fake.calls[0]["tools"] is None
    with pytest.raises(RuntimeError) as empty:
        summarize_span(FakeSummarizer(text="  "), "m", [user("hi")])
    # The reply's shape is named, so the next empty one can be told apart.
    said = str(empty.value)
    assert "empty summary" in said and "finish_reason=" in said and "reasoning_chars=" in said


# -- after a cut ---------------------------------------------------------------------------------


def test_a_provider_is_sent_the_system_prompt_the_summary_block_and_the_kept_tail():
    messages = convo(turns=6)
    state = build_state(messages, provider=FakeSummarizer(text=valid_summary("the gist")), model="m", keep_tokens=_keep(messages, 4))
    assert state is not None and state.user_messages[0] == "request 0"
    sent = apply_to_outbound(messages, state)
    assert sent[0]["role"] == "system"
    block = sent[1]["content"]
    assert "<compacted-history>" in block and "the gist" in block and "request 0" in block
    assert sent[2] is messages[state.boundary_index]  # the tail as it was
    assert len(messages) == 13  # the history itself is unchanged


def test_repeated_compaction_adds_a_part_and_keeps_the_first_as_written():
    """The second cut summarizes only the turns since the first, which stays in the
    block word for word."""
    messages = convo(turns=4)
    fake = FakeSummarizer(text=valid_summary("the first stretch, `fetch_record` renamed"))
    first = build_state(messages, provider=fake, model="m", keep_tokens=_keep(messages, 4))
    for i in range(4, 8):  # the session grows
        messages += [user(f"request {i}"), assistant(f"answer {i} " + "x" * 2000)]
    fake.text = valid_summary("the second stretch")
    second = build_state(messages, provider=fake, model="m", keep_tokens=_keep(messages, 4), prior=first)
    assert second is not None and second.boundary_index > first.boundary_index
    # The second call is shown the first part as context, and told it is kept as it is.
    asked = fake.calls[1]["messages"][1]["content"]
    assert "kept as it is" in asked and "ONLY the part for the conversation after it" in asked
    assert [p["text"] for p in second.parts] == [first.summary_text, valid_summary("the second stretch")]
    assert second.summary_text.startswith("### Part 1 of 2 (oldest)\n" + first.summary_text)
    block = compacted_block(second)
    assert "oldest first" in block and "`fetch_record` renamed" in block
    # and the person's own messages add up from one compaction to the next
    assert "request 0" in second.user_messages[0] and any("request 5" in u for u in second.user_messages)


def test_no_new_cut_is_made_when_nothing_new_falls_before_the_last_one():
    messages = convo(turns=3)
    fake = FakeSummarizer()
    state = build_state(messages, provider=fake, model="m", keep_tokens=_keep(messages, 2))
    assert build_state(messages, provider=fake, model="m", keep_tokens=10_000_000, prior=state) is None


def test_a_saved_state_reads_back_the_same_and_nothing_reads_as_none():
    state = CompactionState(boundary_index=7, summary_text="s", working_state="w", user_messages=["u1"], created_at=1.5, model_used="m", standing_plan=[{"content": "Chart revenue", "status": "in_progress"}])
    assert CompactionState.from_dict(state.as_dict()) == state
    assert CompactionState.from_dict(None) is None and CompactionState.from_dict({}) is None


def test_without_a_state_or_with_one_past_the_history_the_view_is_the_history():
    messages = convo(turns=2)
    assert apply_to_outbound(messages, None) is messages
    assert apply_to_outbound(messages, CompactionState(boundary_index=999, summary_text="s", working_state="")) is messages


@pytest.mark.parametrize(
    ("error", "overflow"),
    [
        ("Error 400: maximum context length is 128000 tokens", True),
        ("context_length_exceeded", True),
        ("Prompt is too long: 210000 tokens > limit", True),
        ("rate limit exceeded", False),
        ("connection reset", False),
    ],
)
def test_a_too_long_request_is_told_apart_from_other_errors(error, overflow):
    assert is_context_overflow(Exception(error)) is overflow


def test_is_context_overflow_reads_each_major_vendors_wording():
    """Every one of these is a 400 whose recovery path is prune → summarize → retry.
    Miss the phrasing and the turn dies on an error the harness could have handled."""
    for body in (
        # OpenAI: the full sentence, which mentions "context length" — but the wording
        # that names the FIX is what a wrapper/relay most often keeps.
        "Please reduce the length of the messages.",
        # Gemini
        "The input token count (139000) exceeds the maximum number of tokens allowed",
        # xAI / Grok
        "maximum prompt length is 131072 but the request contains 145000",
        # Bedrock
        "ValidationException: Input is too long for requested model",
    ):
        assert is_context_overflow(Exception(body)), body
    # And the negative stays negative: a truncation complaint about OUTPUT is not a
    # context overflow (there is nothing to compact).
    assert not is_context_overflow(Exception("max_tokens is too large: must be <= 4096"))


# -- the standing plan --------------------------------------------------------
#
# The bug this section exists for: compaction mid-turn summarized away every `todo_write`
# call that had established the current list, so the model carried on and finished the job
# while the task list on screen stayed frozen at whatever it last saw.


PLAN = [
    {"content": "Load the CSV", "status": "done"},
    {"content": "Chart revenue by region", "status": "in_progress"},
    {"content": "Write the summary", "status": "pending"},
]


def _planned_block():
    return compacted_block(CompactionState(boundary_index=4, summary_text="s", working_state="", standing_plan=normalize_plan(PLAN)))


def test_the_plan_reaches_the_block_with_every_status_intact():
    block = _planned_block()
    assert "[x] Load the CSV" in block and "[>] Chart revenue by region" in block and "[ ] Write the summary" in block


def test_the_block_asks_for_the_call_and_not_only_the_list():
    """The list alone fixes the MODEL. The panel is fed by `todo_write` calls, so without
    an instruction to re-issue it the person watching still sees a frozen checklist."""
    assert "todo_write" in _planned_block()


def test_no_plan_means_no_section():
    """A session that never planned must not be told to re-issue an empty list."""
    assert "Standing plan" not in compacted_block(CompactionState(boundary_index=4, summary_text="s", working_state=""))
    assert render_standing_plan([]) == ""


def test_the_plan_is_what_the_engine_injects_not_what_the_span_held():
    """The span is the wrong source. A plan written before the boundary and UPDATED after
    it would put a stale list in a block sitting in front of a tail that contradicts it."""
    messages = convo(turns=6)
    stale = [{"content": "Chart revenue by region", "status": "pending"}]
    messages[3:3] = tool_turn("todo_write", {"todos": stale}, {"count": 1, "todos": stale})
    state = build_state(messages, provider=FakeSummarizer(), model="m", keep_tokens=_keep(messages, 4), standing_plan=PLAN)
    assert state is not None
    block = compacted_block(state)
    assert "[>] Chart revenue by region" in block  # the live status, not the span's
    assert "[ ] Chart revenue by region" not in block


def test_a_plan_survives_repeated_compaction_as_the_list_stands_then():
    messages = convo(turns=4)
    fake = FakeSummarizer()
    first = build_state(messages, provider=fake, model="m", keep_tokens=_keep(messages, 2), standing_plan=PLAN)
    messages += [user("and now the appendix"), assistant("on it " + "y" * 2000)]
    done = [dict(item, status="done") for item in PLAN]
    second = build_state(messages, provider=fake, model="m", keep_tokens=_keep(messages, 2), prior=first, standing_plan=done)
    assert second is not None
    block = compacted_block(second)
    assert "[x] Chart revenue by region" in block and "[>] Chart revenue by region" not in block


def test_a_malformed_entry_costs_that_entry_and_not_the_block():
    """A plan is a display: the tool coerces on the way in, and so does this."""
    assert normalize_plan([{"content": "  Real work ", "status": "nonsense"}, {"content": ""}, "bare string"]) == [
        {"content": "Real work", "status": "pending"},
        {"content": "bare string", "status": "pending"},
    ]
    assert normalize_plan(None) == [] and normalize_plan("nope") == []


def test_a_runaway_plan_is_bounded_and_says_so():
    rendered = render_standing_plan(normalize_plan([{"content": f"step {i}", "status": "pending"} for i in range(60)]))
    assert "step 39" in rendered and "step 40" not in rendered and "20 further items omitted" in rendered
