"""Auto-compaction: the token budget, the compaction state, the summarizer, and the outbound
view. `tokens` has the arithmetic and the prune, `record` the state, `boundary` where to cut,
`extraction` what is taken mechanically, `plans` the plans a block carries, `parts` the cuts a block
is made of, `summarizer` the model call, and `block` the view that is sent."""
from __future__ import annotations

from .arguments import *  # noqa: F401,F403
from .asking import ask_text, settle_parts
from .block import (
    BLOCK_OPENING,
    apply_to_outbound,
    block_segments,
    compacted_block,
    offload_images,
    replace_offloaded_images,
)
from .boundary import pick_boundary
from .extraction import cap_user_messages, extract_user_messages, extract_working_state, iter_tool_calls
from .fold import *  # noqa: F401,F403
from .parts import *  # noqa: F401,F403
from .plans import clip_approved_plan, normalize_plan, render_standing_plan
from .record import CompactionNotice, CompactionState, compaction_gate
from .summarizer import (
    COMPACTION_INSTRUCTION,
    CONTINUATION_CONTRACT,
    NO_TOOLS_NOTICE,
    SUMMARY_CORRECTION,
    SUMMARY_SECTIONS,
    SUMMARY_SYSTEM_PROMPT,
    build_state,
    compaction_instruction,
    summarize_span,
    summarizer_messages,
    summary_missing_sections,
)
from .rederive import *  # noqa: F401,F403
from .tokens import *  # noqa: F401,F403
