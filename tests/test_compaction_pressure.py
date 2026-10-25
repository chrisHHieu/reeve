"""Six things the context manager was doing wrong, found by reading other designs next to ours.

The comparison was worth running: on the four mechanisms both harnesses have — the 0.8
threshold, an untouched canonical history, a cache-reusing summarizer prefix, and
compact-then-retry on a provider overflow — reeve was already level or ahead. What it
lacked was narrower and, once named, cheap:

  6.1  ONE lever, and it was the expensive one. A tool result is bounded when it is
       produced (run_shell caps at 20_000 chars) and never reconsidered, so forty stale
       shell calls sat in the window at full size and the only way out was to pay a
       summarizer for the whole span. Pruning aged results keeps their two ends for free.
  6.2  The pressure signal was a whole step stale: the provider's number for the LAST
       request, with nothing added for the turn and tool results appended since.
  6.3  Nothing checked that a compaction made the context SMALLER.
  6.4  A `enabled is False` gate no code, no setting and no test could ever reach.
  6.5  Switching to a bigger model left the session compacted forever, although the
       canonical history was sitting there intact the whole time.
  6.6  The compacted block was a fixed ~10k tokens whatever the window — a fifth of a 32k
       model, and on a small enough one larger than the trigger it was supposed to fit
       under, which is a compaction that can never converge.

Pruning is deliberately NOT continuous, and that is the finding worth keeping: providers
match a cached prefix byte for byte, so rewriting a message the model has already seen
throws the cache away from that point. Doing it every request would trade a summarizer
call for a cache miss every few steps. Doing it at the moment compaction was going to
fire is free, because compaction invalidates the prefix anyway.
"""

from __future__ import annotations

import json

import pytest

from summary_fixtures import valid_summary

from harness.context import compaction as C


def _tool(call_id: str, size: int) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": "x" * size}


def _assistant(call_id: str, name: str = "run_shell") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "function": {"name": name, "arguments": json.dumps({"command": "ls"})},
            }
        ],
    }


def _session(steps: int, *, result_chars: int = 20_000) -> list[dict]:
    """A shell-heavy session: the shape that fills a window in practice."""
    messages: list[dict] = [{"role": "system", "content": "you are a coding agent"}]
    messages.append({"role": "user", "content": "audit the repository"})
    for i in range(steps):
        messages.append(_assistant(f"c{i}"))
        messages.append(_tool(f"c{i}", result_chars))
    return messages


# -- 6.1 the cheap lever -------------------------------------------------------------


def test_aged_tool_results_keep_their_two_ends():
    messages = _session(3)
    pruned = C.prune_tool_results(messages, len(messages), budget=5_000)

    body = pruned[3]["content"]
    assert len(body) <= 5_000
    assert body.startswith("xxx") and body.endswith("xxx")
    # The gap is NAMED in the text the model reads: a result silently shortened is one
    # the model reasons about as though it were whole.
    assert "x" * 20_000 not in body
    assert any(ch.isdigit() for ch in body), f"no gap marker in {body[4000:4200]!r}"


def test_the_newest_results_are_left_alone():
    """Other designs prune every oversized result on the surface. A result from the current step
    is what the agent is working WITH; one from thirty steps ago is what it half-remembers.
    """
    messages = _session(6)
    upto = C.prune_boundary(messages, keep_tokens=12_000)
    pruned = C.prune_tool_results(messages, upto)

    assert len(pruned[-1]["content"]) == 20_000, "the newest result must be untouched"
    assert len(pruned[3]["content"]) < 20_000, "the oldest must not be"


def test_pruning_never_changes_the_message_count():
    """`boundary_index` indexes this list. A projection that dropped messages would move
    the boundary under everything that cites it."""
    messages = _session(5)
    pruned = C.prune_tool_results(messages, len(messages))
    assert len(pruned) == len(messages)
    assert [m["role"] for m in pruned] == [m["role"] for m in messages]


def test_pruning_does_not_mutate_the_canonical_history():
    messages = _session(3)
    before = [dict(m) for m in messages]
    C.prune_tool_results(messages, len(messages))
    assert messages == before


def test_a_short_result_is_returned_untouched():
    messages = _session(2, result_chars=100)
    pruned = C.prune_tool_results(messages, len(messages))
    assert pruned[3]["content"] == "x" * 100
    assert pruned[3] is messages[3], "an untouched message should not even be copied"


def test_nothing_aged_means_nothing_pruned():
    messages = _session(2)
    assert C.prune_boundary(messages, keep_tokens=10**9) == 0
    assert C.prune_tool_results(messages, 0) is messages


def test_pruning_is_what_makes_the_window_fit_again():
    """The point of the whole item: enough saving to skip the summarizer, with no model
    call and nothing that a re-read could not recover."""
    messages = _session(20)
    full = C.estimate_tokens(messages)
    upto = C.prune_boundary(messages, keep_tokens=20_000)
    saved = full - C.estimate_tokens(C.prune_tool_results(messages, upto))
    assert saved > full * 0.5, f"only saved {saved} of {full}"


def test_a_pruned_state_survives_a_round_trip():
    state = C.CompactionState(
        boundary_index=0, summary_text="", working_state="", pruned_upto=42
    )
    assert C.CompactionState.from_dict(state.as_dict()).pruned_upto == 42


def test_a_state_written_before_pruning_existed_reads_as_unpruned():
    old = {"boundary_index": 7, "summary_text": "s", "working_state": ""}
    assert C.CompactionState.from_dict(old).pruned_upto == 0


def test_the_outbound_view_applies_pruning_without_a_summary():
    """`boundary_index == 0` is a session that has only ever been pruned — the cheap
    lever must not require the expensive one to have run first."""
    messages = _session(6)
    state = C.CompactionState(
        boundary_index=0,
        summary_text="",
        working_state="",
        pruned_upto=C.prune_boundary(messages, keep_tokens=12_000),
    )
    out = C.apply_to_outbound(messages, state)
    assert len(out) == len(messages)  # nothing summarized away
    assert C.estimate_tokens(out) < C.estimate_tokens(messages)
    assert "<compacted-history>" not in json.dumps(out)


def test_pruning_and_summarizing_compose():
    messages = _session(10)
    state = C.CompactionState(
        boundary_index=12,
        summary_text="earlier work",
        working_state="",
        pruned_upto=len(messages),
    )
    out = C.apply_to_outbound(messages, state)
    assert out[0]["role"] == "system"
    assert "<compacted-history>" in out[1]["content"]
    # The retained tail is pruned too, which is the entire reason both exist.
    tail_tools = [m for m in out[2:] if m.get("role") == "tool"]
    assert tail_tools and all(len(m["content"]) <= 5_000 for m in tail_tools)


# -- 6.3 a compaction that grows the context is not a compaction ----------------------


class _Provider:
    def __init__(self, text: str) -> None:
        self.text = text

    def complete(self, **_kw):
        class _Turn:
            pass

        turn = _Turn()
        turn.text = self.text
        turn.usage = None
        return turn


def test_a_summary_larger_than_what_it_replaces_is_refused():
    """Nothing bounds what a summarizer returns except a max_tokens the provider may
    ignore. Getting bigger is the silent kind of wrong: the next check sees MORE pressure,
    compacts again, and the session spends round-trips going backwards."""
    # Valid sections (or the structure check would fire first), one of them oversized.
    oversized = valid_summary("ok").replace(
        "## Current work\nok", "## Current work\n" + "y" * 400_000
    )
    messages = _session(2, result_chars=50)
    with pytest.raises(RuntimeError, match="would not shrink"):
        C.build_state(
            messages,
            provider=_Provider(oversized),
            model="m",
            keep_tokens=50,
        )


def test_an_ordinary_summary_is_accepted():
    messages = _session(8)
    state = C.build_state(
        messages,
        provider=_Provider(valid_summary("A short, honest summary of the work so far.")),
        model="m",
        keep_tokens=2_000,
    )
    assert state is not None and state.boundary_index > 0


# -- 6.6 the block has to fit the window it frees -------------------------------------


def test_the_block_shrinks_with_the_window():
    small, big = C.block_limits(25_000), C.block_limits(250_000)
    assert small[0] < big[0], "summary budget must scale"
    assert small[1] < big[1], "the user-message list must scale"


def test_a_large_window_keeps_the_historical_limits():
    """The numbers that were there are right for a big model — this item is about small
    ones, and changing behaviour where nothing was wrong would be its own regression."""
    assert C.block_limits(250_000) == (C.SUMMARY_MAX_TOKENS, C._USER_MESSAGES_MAX)


def test_the_block_stays_inside_its_share_of_a_small_window():
    """The failure this prevents is not "a bit large" - it is a block BIGGER than the
    trigger it has to fit under, which makes compaction unable to converge: every pass
    frees history and hands back a block that keeps the session over the line."""
    trigger = 8_000
    summary_tokens, users = C.block_limits(trigger)
    state = C.CompactionState(
        boundary_index=5,
        summary_text="s" * (summary_tokens * 4),
        working_state="",
        user_messages=["u" * C._USER_MESSAGE_CLIP] * users,
    )
    block = C.estimate_tokens([{"role": "user", "content": C.compacted_block(state)}])
    assert block < trigger, f"block is {block} tokens against a {trigger} trigger"
    # And inside the share it claims, not merely under the line by luck.
    assert block <= trigger * C.BLOCK_BUDGET_FRACTION * 1.25, block


def test_the_block_never_shrinks_to_nothing():
    """A block with no summary and no user messages is not a smaller block; it is a
    compaction that lost the two things worth keeping."""
    summary_tokens, users = C.block_limits(1_000)
    assert summary_tokens >= 500 and users >= 5


# -- the opening message is not "old" ------------------------------------------------


def test_the_first_user_message_survives_a_tight_budget():
    """Found by two existing tests going red, and they were right to.

    Newest-N was safe while the limit was a fixed 40. Sizing the block against the window
    made small limits real, and newest-N then drops the OPENING message — the one that
    says what the session is for, and the exact thing this list exists to preserve
    without trusting a model to remember it.
    """
    said = [f"step {i}" for i in range(30)]
    kept, dropped = C.cap_user_messages(said, prior_dropped=0, limit=5)

    assert kept[0] == "step 0", "the request that started the session must survive"
    assert kept[-1] == "step 29", "so must the most recent one"
    assert len(kept) == 5
    assert dropped == 25


def test_the_dropped_count_stays_honest_across_compactions():
    kept, dropped = C.cap_user_messages(
        [f"s{i}" for i in range(10)], prior_dropped=7, limit=4
    )
    assert dropped == 13 and len(kept) == 4


def test_a_list_inside_the_limit_is_untouched():
    said = ["a", "b", "c"]
    kept, dropped = C.cap_user_messages(said, prior_dropped=0, limit=40)
    assert kept == said and dropped == 0


def test_a_limit_of_one_keeps_the_opening_message():
    kept, dropped = C.cap_user_messages(["first", "second", "third"], prior_dropped=0, limit=1)
    assert kept == ["first"] and dropped == 2


def test_a_small_window_tells_the_summarizer_its_length_instead_of_cutting_it_off():
    """Harness eval, 2026-09-24: on a 40k trigger the block budget made the summarizer's
    max_tokens 2,000; reasoning models spent it thinking and returned empty or
    section-less summaries, so no compaction ever landed. The budget is now the length the
    model is TOLD; the call keeps room to think."""
    from harness.llm.providers import AssistantTurn

    seen = {}

    class _Summarizer:
        def complete(self, *, model, messages, tools=None, **settings):
            seen["max_tokens"] = settings.get("max_tokens")
            seen["last"] = messages[-1]["content"]
            text = "\n".join(f"## {name}\nx" for name in C.SUMMARY_SECTIONS)
            return AssistantTurn(text=text, finish_reason="stop")

    history = [{"role": "system", "content": "s"}]
    for i in range(12):
        history += [
            {"role": "user", "content": f"request {i} " + "y" * 4_000},
            {"role": "assistant", "content": f"answer {i} " + "z" * 4_000},
        ]
    target, _users = C.block_limits(40_000)
    state = C.build_state(
        history, provider=_Summarizer(), model="m", keep_tokens=2_000, trigger=40_000
    )
    assert state is not None
    assert seen["max_tokens"] == C.summary_call_tokens(target)
    assert seen["max_tokens"] >= C.SUMMARY_MAX_TOKENS + C.SUMMARY_REASONING_HEADROOM
    assert f"under about {int(target * 0.75)} words" in seen["last"]


def test_the_summarizer_never_asks_past_the_models_output_limit():
    """The reasoning headroom lifts max_tokens to 16k; a model that declares an 8k output
    limit (older Gemini Flash, many local models) would refuse the request outright and
    every compaction would fail. The call is clamped to what the model declares."""
    from harness.llm.providers import AssistantTurn

    seen = {}

    class _Summarizer:
        def complete(self, *, model, messages, tools=None, **settings):
            seen["max_tokens"] = settings.get("max_tokens")
            text = "\n".join(f"## {name}\nx" for name in C.SUMMARY_SECTIONS)
            return AssistantTurn(text=text, finish_reason="stop")

    history = [{"role": "system", "content": "s"}]
    for i in range(12):
        history += [
            {"role": "user", "content": f"request {i} " + "y" * 4_000},
            {"role": "assistant", "content": f"answer {i} " + "z" * 4_000},
        ]
    C.build_state(
        history, provider=_Summarizer(), model="m", keep_tokens=2_000, trigger=40_000,
        output_limit=8_192,
    )
    assert seen["max_tokens"] == 8_192
    C.build_state(history, provider=_Summarizer(), model="m", keep_tokens=2_000, trigger=40_000)
    assert seen["max_tokens"] > 8_192  # no declared limit: the full headroom
