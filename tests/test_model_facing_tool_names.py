"""Model-facing text names only tools and arguments that exist in this registry.

Found by the 2026-09-24 harness audit: text ported verbatim from elsewhere told the model
to use "the Read or Glob tool", "Grep", "Bash", and "Read with offset/limit" — tools and
arguments Reeve does not have. The worst copy was the truncation hint, read at the
exact moment a result has just lost its middle. The shapes below are the ones that
shipped; the registry is the real session's, built the way the app builds it.
"""

from __future__ import annotations

import re

import pytest

from harness.compose import build_engine
from harness.modes.agents.reeve import REEVE_INSTRUCTIONS
from harness.modes.agents.registry import get_agent
from harness.loop.nudges import truncation_hint
from harness.tools.task import DESCRIPTION
from harness.tools.task_agents import EXPLORE_PROMPT

#: Other tools' tool names that are not Reeve's, in the phrasings that carry
#: an instruction ("use the Read tool", "Use Grep for …", "Read with offset/limit").
_FOREIGN = [
    # "its Bash tool" is allowed: the claude-code subagent's description names ITS OWN tool.
    re.compile(r"(?<!its )\b(?:Read|Glob|Grep|Bash|LS|MultiEdit|WebFetch|TodoWrite) tool\b"),
    re.compile(r"\bUse (?:Read|Glob|Grep|Bash)\b"),
    re.compile(r"\b(?:Grep|Read) (?:and|or|with) (?:Read|Glob|Grep|offset)\b"),
    re.compile(r"offset/limit"),
]


@pytest.fixture(scope="module")
def registry(tmp_path_factory):
    state = tmp_path_factory.mktemp("state")
    ws = tmp_path_factory.mktemp("ws")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("REEVE_STATE_DIR", str(state))
        engine = build_engine(agent=get_agent("reeve"), workspace=ws, session_id="names")
    return engine.registry


def _texts(registry) -> dict[str, str]:
    texts = {
        "task.DESCRIPTION": DESCRIPTION,
        "EXPLORE_PROMPT": EXPLORE_PROMPT,
        "hint(delegate)": truncation_hint(True, "x.log", 90_000),
        "hint(read)": truncation_hint(False, "x.log", 90_000),
        "REEVE_INSTRUCTIONS": REEVE_INSTRUCTIONS,
    }
    for schema in registry.all_schemas():
        fn = schema.get("function") or schema
        texts[f"schema:{fn.get('name')}"] = repr(schema)
    return texts


def test_no_model_facing_text_names_a_foreign_tool(registry):
    offenders = [
        (where, rx.pattern)
        for where, text in _texts(registry).items()
        for rx in _FOREIGN
        if rx.search(text)
    ]
    assert offenders == []


def test_the_tools_the_hints_and_prompts_name_are_registered(registry):
    names = set(registry.names())
    for tool in ("read_file", "list_files", "grep", "run_shell", "task"):
        assert tool in names, tool
    for text in (DESCRIPTION, EXPLORE_PROMPT, truncation_hint(False, "x.log", 90_000)):
        for mentioned in re.findall(r"\b(read_file|list_files|grep|run_shell)\b", text):
            assert mentioned in names
