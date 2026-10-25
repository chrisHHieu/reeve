"""Numbers that bound a turn: retries, silence, output continuation and result sizes."""

from __future__ import annotations

# Attempts after the first for a stream that died mid-answer. Two on purpose: that is what the
# OpenAI and Anthropic SDKs already spend on a request failing before its first chunk. Matching it
# gives both sides of that byte the same budget, which was the whole defect: an answer cut at
# byte 1 got two more tries and one cut at byte 40,000 got none.
#
# The loop does not read this. It asks `providers.errors.retry_budget(exc)`, which prices each
# failure class separately (rate_limit 4, overloaded 4, transport 2, empty 2), and for the two
# classes a mid-stream death falls into the answer is this number. That is why tests could assert
# against this constant and pass without it being consulted. The agreement is real but was
# accidental, so `test_stream_retry.py` pins it: change `_RETRY_BUDGETS` and that test fails, rather
# than this comment describing behaviour the runtime no longer has.
STREAM_RETRIES = 2

# Short on purpose: someone is watching a half-written answer sit there. The provider's own
# Retry-After wins when it sent one (providers/errors.py).
BACKOFF_SECONDS = (0.5, 1.5)

# How long the provider may send nothing at all (no text, no reasoning, no tool fragment, no final
# turn) before the stream is declared dead and re-issued on the retry path. The SDKs bound the whole
# request, ten minutes by default, which leaves a wedged call in front of the person for ten
# minutes; this bounds the silence instead, which is what a stalled connection produces. It is
# generous because reasoning models can think for minutes before the first token, and a watchdog
# that fires on a slow but live stream turns a working turn into a duplicate request. Only a gap in
# bytes counts, and the timer resets on every chunk.
STREAM_IDLE_SECONDS = 300.0

# How many times one step may be extended after the provider cut it at the output-token limit. A cut
# answer is not a failure the model can fix by splitting: the person asked for one message and the
# model was already writing it. So the engine asks for the rest of the same message and does not end
# the turn on half an answer. After three tries the answer is longer than any single message should
# be, and the honest report is a truncated turn and not a fourth request.
TRUNCATION_CONTINUES = 3

# The aggregate tool-output budget of one step. The per-tool caps bound one result, but thirty
# medium results from one response add up to the same overflowing request. Past the cap the newest
# oversized result keeps its head and a spill locator, the shape a compaction fold produces, paid
# when the result is written and not when it is pruned.
#
# The sum used to run over the whole user turn and it shrank `read_file` too. Measured on the real
# session store (2026-09-24 audit, `scripts/harness_kpis.py`): 12 turns crossed it and then ran a
# median 8.5 more steps with every result a 4k stub; 27 reads were stubbed, 24 were read again in the
# same turn and 13 of those came back stubbed again. A long turn is exactly where reading has to keep
# working, and growth of history across steps is compaction's job. Self-bounded tools
# (`ToolContract.self_bounded`) are exempt.
STEP_OUTPUT_BUDGET = 120_000
SHRUNK_RESULT_BUDGET = 4_000
