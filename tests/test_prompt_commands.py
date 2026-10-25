"""The prompt commands (`/init`, `/review`) name tools this app really has.

The templates live in the GUI (`surfaces/gui/src/promptCommands.ts`) and are read by the
model, which calls what they name. The first `/init` told it to use a tool `ask`; the tool
is `ask_user`, so the one instruction about asking the person could not be followed. Text
in one language naming tools registered in another has nothing to keep it true but a check
that reads both.
"""

from __future__ import annotations

import re
from pathlib import Path

from harness.compose import build_engine
from harness.modes.agents.reeve import reeve_agent

SOURCE = Path(__file__).resolve().parents[1] / "surfaces" / "gui" / "src" / "promptCommands.ts"

#: A backticked snake_case word with an underscore: in these templates that is a tool name
#: and nothing else (commands have spaces, paths have dots or slashes).
_NAMED = re.compile(r"`([a-z]+(?:_[a-z]+)+)`")


def _template(name: str) -> str:
    source = SOURCE.read_text(encoding="utf-8")
    start = source.index(f"const {name} = [")
    return source[start : source.index('].join("\\n");', start)]


def _tools(tmp_path) -> set[str]:
    async def ask(_question, _context=None):
        return {"answer": ""}

    engine = build_engine(
        agent=reeve_agent(), workspace=tmp_path, provider=object(), question_asker=ask
    )
    return set(engine.registry.names())


def test_every_tool_a_template_names_is_registered(tmp_path):
    tools = _tools(tmp_path)
    for template in ("INIT_TEMPLATE", "REVIEW_TEMPLATE"):
        named = set(_NAMED.findall(_template(template)))
        assert named, f"{template} names no tool: the pattern stopped matching"
        assert named <= tools, f"{template} names {sorted(named - tools)}, which this app does not have"


def test_init_asks_the_person_through_the_tool_that_exists(tmp_path):
    template = _template("INIT_TEMPLATE")
    assert "`ask_user`" in template
    assert "`ask`" not in template
    assert "ask_user" in _tools(tmp_path)


def test_the_rules_directory_init_writes_to_is_the_one_the_engine_reads(tmp_path):
    """`/init` tells the model where a path-scoped rule goes and what its first lines are.
    A file written that way has to be one the engine loads for a matching path."""
    from harness.context.project_rules import nested_rules

    template = _template("INIT_TEMPLATE")
    assert ".agents/rules/<name>.md" in template and "paths:" in template
    rules = tmp_path / ".agents" / "rules"
    rules.mkdir(parents=True)
    (rules / "api.md").write_text("---\npaths:\n  - src/api/**\n---\n\nAPI RULE\n", encoding="utf-8")
    (tmp_path / "src" / "api").mkdir(parents=True)
    handler = tmp_path / "src" / "api" / "users.py"
    handler.write_text("x = 1\n", encoding="utf-8")

    assert [key for key, _ in nested_rules(tmp_path, [str(handler)])] == ["api.md"]
