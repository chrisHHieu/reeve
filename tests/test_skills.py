"""The agents and the SKILL.md loader: what tools an agent is built with, which id leads to which
agent, how skills are found and offered (the catalog and `load_skill`), and what an engine built for
each agent holds.
"""

from __future__ import annotations

import logging

import pytest

from harness.compose import build_engine
from harness.compose.session_helpers import skill_dirs as _skill_dirs
from harness.extensions.skills import SkillLoader, default_skill_roots, skill_catalog_text, skill_tools
from harness.extensions.skills.base import _CATALOG_DESCRIPTION_MAX_CHARS
from harness.llm.providers import ModelCapabilities
from harness.modes.agents import AgentContext, chat_agent, reeve_agent, get_agent
from harness.safety.secrets import state_dir
from harness.tools import ToolRegistry
from harness.tools.shell import LocalExecutor
from harness.tools.todo import TodoList


class _NeverRuns:
    """A model client for engines that are built and never run."""

    def complete(self, **request):  # pragma: no cover
        raise AssertionError("no turn is run")

    def capabilities(self, model):
        return ModelCapabilities(tools=True)


# -- the agents ---------------------------------------------------------------------------------


def test_the_agent_is_built_with_the_file_shell_and_todo_tools(tmp_path):
    shell = LocalExecutor(cwd=tmp_path, default_timeout=5)
    try:
        built = reeve_agent().build_tools(AgentContext(workspace=tmp_path, executor=shell, todo=TodoList()))
        assert {"read_file", "write_file", "run_shell", "todo_write"} <= {getattr(tool, "__name__", "?") for tool in built}
    finally:
        shell.close()


def test_chat_has_no_workspace_tools_and_neither_agent_needs_a_folder():
    assert chat_agent().build_tools(AgentContext()) == [] and chat_agent().requires_folder is False
    # Code joined the one agent (2026-09-20), which needs no folder: it starts in scratch space and works in
    # a repository once pointed at one
    assert reeve_agent().requires_folder is False


@pytest.mark.parametrize(
    "agent_id",
    [
        "chat",  # removed (owner, 2026-08-21): like any unknown id it falls to the default
        "nope",
        "code",  # joined the one agent (2026-09-20): a saved repository session opens with git, terminals and the joined prompt
    ],
)
def test_an_old_or_unknown_agent_id_opens_the_one_agent(agent_id):
    assert get_agent(agent_id).name == "reeve"


# -- finding and offering skills -------------------------------------------------------------------


def _skill(skills_dir, name, desc, body):
    folder = skills_dir / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n{body}", encoding="utf-8")


def test_a_skill_is_listed_in_the_menu_and_loaded_by_name(tmp_path):
    _skill(tmp_path / "skills", "pdf", "extract text from PDFs", "Use pdfplumber to extract text.")
    loader = SkillLoader([tmp_path / "skills"])
    assert loader.catalog() == [{"name": "pdf", "description": "extract text from PDFs", "when_to_use": "", "model_invocable": True, "user_invocable": True, "context": "main"}]
    menu = skill_catalog_text(loader)
    assert "<available_skills>" in menu
    assert "- `pdf`: extract text from PDFs" in menu and menu.endswith("</available_skills>")
    registry = ToolRegistry()
    registry.register_all(skill_tools(loader))
    assert "pdfplumber" in registry.execute("load_skill", {"name": "pdf"})["instructions"]
    assert registry.execute("load_skill", {"name": "missing"})["error"]


def test_load_skill_presentation_carries_the_body_as_a_card(tmp_path):
    skills_dir = tmp_path / "skills"
    _skill(
        skills_dir, "pdf", "extract text from PDFs", "Use pdfplumber to extract text."
    )
    loader = SkillLoader([skills_dir])
    reg = ToolRegistry()
    reg.register_all(skill_tools(loader))

    spec = reg.get("load_skill")
    value = reg.execute("load_skill", {"name": "pdf"})
    presentation = spec.contract.present_result({"name": "pdf"}, value, None)

    assert presentation.kind == "read"
    card = presentation.card
    assert card["card"] == "skill"
    assert card["name"] == "pdf"
    assert card["description"] == "extract text from PDFs"
    assert "pdfplumber" in card["instructions"]


# -- frontmatter parsing (P0-1) ---------------------------------------------------


def _write_skill_md(skills_dir, folder, text):
    d = skills_dir / folder
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    return d


def test_quoted_colon_and_block_scalar_frontmatter(tmp_path):
    # CRLF + delimiter-looking text inside YAML
    # values must not truncate the block.
    skills_dir = tmp_path / "skills"
    _write_skill_md(
        skills_dir,
        "quoted",
        '---\nname: quoted\ndescription: "Extract: text, images, and tables"\n---\n\nUse pdfplumber.\n',
    )
    _write_skill_md(
        skills_dir,
        "block",
        "---\nname: block\ndescription: |\n  Includes a ---- marker that is not a delimiter.\n---\n\nBlock body.\n",
    )
    loader = SkillLoader([skills_dir])
    assert loader.get("quoted").description == "Extract: text, images, and tables"
    assert (
        loader.get("block").description
        == "Includes a ---- marker that is not a delimiter."
    )
    assert loader.get("block").instructions == "Block body."


def test_bom_and_crlf_frontmatter(tmp_path):
    # the file must not be
    # skipped just because `---` is preceded by \uFEFF.
    skills_dir = tmp_path / "skills"
    d = skills_dir / "bom-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "\ufeff---\r\nname: bom-skill\r\ndescription: saved by Notepad\r\n---\r\n\r\nBOM body.\r\n",
        encoding="utf-8",
    )
    skill = SkillLoader([skills_dir]).get("bom-skill")
    assert skill.description == "saved by Notepad"
    assert skill.instructions == "BOM body."


def test_unterminated_frontmatter_treated_as_whole_body(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_skill_md(
        skills_dir, "broken", "---\nname: broken\ndescription: never closed"
    )
    skill = SkillLoader([skills_dir]).get("broken")
    assert skill.name == "broken"  # folder fallback
    assert skill.description == ""
    assert "never closed" in skill.instructions


def test_legacy_line_frontmatter_falls_back(tmp_path):
    # P0-1 legacy fallback: the old writer emitted raw values, so `description: says: hello`
    # is invalid YAML. It must keep loading (and keep its value).
    skills_dir = tmp_path / "skills"
    _write_skill_md(
        skills_dir,
        "legacy",
        "---\nname: legacy\ndescription: says: hello\n---\n\nLegacy body.\n",
    )
    loader = SkillLoader([skills_dir])
    skill = loader.get("legacy")
    assert skill.description == "says: hello"
    assert skill.instructions == "Legacy body."
    assert "- `legacy`: says: hello" in skill_catalog_text(loader)


def test_corrupt_file_is_contained_and_warned(tmp_path, caplog):
    # a bad file never hides valid siblings.
    skills_dir = tmp_path / "skills"
    _skill(skills_dir, "good", "fine", "Good body.")
    bad = skills_dir / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_bytes(b"\xff\xfe\x00 not utf-8 \xff")
    with caplog.at_level(logging.WARNING):
        loader = SkillLoader([skills_dir])
    assert loader.names() == ["good"]
    assert loader.get("bad") is None
    assert any(
        "skill ignored:" in record.getMessage() and "bad" in record.getMessage()
        for record in caplog.records
    )


def test_transient_read_failure_keeps_valid_siblings(tmp_path, monkeypatch, caplog):
    # a transient read is reported, never fatal
    # to the rest of the catalog.
    from pathlib import Path as _Path

    skills_dir = tmp_path / "skills"
    _skill(skills_dir, "stable", "Stable skill", "Stable body.")
    _skill(skills_dir, "flaky", "Flaky skill", "Flaky body.")
    original = _Path.read_text

    def flaky_read(self, *args, **kwargs):
        if self.name == "SKILL.md" and self.parent.name == "flaky":
            raise OSError("transient read failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(_Path, "read_text", flaky_read)
    with caplog.at_level(logging.WARNING):
        loader = SkillLoader([skills_dir])
    assert loader.names() == ["stable"]
    assert any(
        "skill ignored:" in record.getMessage() and "flaky" in record.getMessage()
        for record in caplog.records
    )


# -- name policy at load (P1-1) ---------------------------------------------------


def test_name_policy_warns_but_keeps(tmp_path, caplog):
    # An invalid name is warned about and kept, as is a folder
    # mismatch. We warn and keep — legacy CRUD names must stay usable.
    skills_dir = tmp_path / "skills"
    _write_skill_md(
        skills_dir, "My Skill", "---\nname: My Skill\ndescription: legacy name\n---\nbody\n"
    )
    _write_skill_md(
        skills_dir,
        "folder-name",
        "---\nname: other-name\ndescription: mismatch\n---\nbody\n",
    )
    with caplog.at_level(logging.WARNING):
        loader = SkillLoader([skills_dir])
    assert set(loader.names()) == {"My Skill", "other-name"}  # kept, never dropped
    messages = [record.getMessage() for record in caplog.records]
    assert any("not lowercase-kebab-case" in m for m in messages)
    assert any("does not match its folder" in m for m in messages)


# -- flat <name>.md discovery (P1-3) -----------------------------------------------


def test_flat_md_skill_is_discovered_with_its_directory_as_resource_base(tmp_path):
    # A bare .md beside the folders is a skill.
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "one-pager.md").write_text(
        "---\nname: one-pager\ndescription: flat skill\n---\n\nFlat body.\n",
        encoding="utf-8",
    )
    loader = SkillLoader([skills_dir])
    skill = loader.get("one-pager")
    assert skill.instructions == "Flat body."
    assert skill.path == str(skills_dir)  # resource base = the containing directory


def test_flat_md_discovery_does_not_recurse(tmp_path):
    skills_dir = tmp_path / "skills"
    nested = skills_dir / "nested"
    nested.mkdir(parents=True)
    (nested / "deep.md").write_text(
        "---\nname: deep\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    assert "deep" not in SkillLoader([skills_dir]).names()  # NO nested **/SKILL.md


def test_flat_md_without_name_is_skipped_with_warning(tmp_path, caplog):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "nameless.md").write_text(
        "---\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING):
        loader = SkillLoader([skills_dir])
    assert loader.names() == []
    assert any(
        "skill ignored:" in record.getMessage()
        and "nameless.md" in record.getMessage()
        for record in caplog.records
    )


# -- catalog contract (P0-3/P0-4/P1-4) ----------------------------------------------


def test_undescribed_skill_hidden_from_catalog_but_loadable(tmp_path):
    # discovered, loadable, but absent
    # from the model-facing catalog.
    skills_dir = tmp_path / "skills"
    _write_skill_md(
        skills_dir, "manual", "---\nname: manual\n---\n\n# Manual\n\nInstructions here.\n"
    )
    loader = SkillLoader([skills_dir])
    assert loader.catalog() == [
        {
            "name": "manual",
            "description": "",
            "when_to_use": "",
            "model_invocable": True,
            "user_invocable": True,
            "context": "main",
        }
    ]
    assert skill_catalog_text(loader) == ""  # the text for no skills

    reg = ToolRegistry()
    reg.register_all(skill_tools(loader))
    loaded = reg.execute("load_skill", {"name": "manual"})
    assert "Instructions here." in loaded["instructions"]


def test_catalog_description_normalized_and_capped(tmp_path):
    # (the cap is a real contract): 2000 chars in,
    # whitespace-normalized, 500 chars out INCLUDING the ellipsis.
    skills_dir = tmp_path / "skills"
    _skill(skills_dir, "long", "word " * 400, "body")
    _write_skill_md(
        skills_dir,
        "wrapped",
        "---\nname: wrapped\ndescription: |-\n  first line\n  second line\n---\nbody\n",
    )
    text = skill_catalog_text(SkillLoader([skills_dir]))
    lines = text.splitlines()
    long_line = next(line for line in lines if line.startswith("- `long`:"))
    capped = long_line[len("- `long`: ") :]
    assert len(capped) == _CATALOG_DESCRIPTION_MAX_CHARS
    assert capped.endswith("...")
    # Normalization collapses the block scalar's newline into one space.
    assert "- `wrapped`: first line second line" in lines


def test_catalog_escapes_xml(tmp_path):
    # an author cannot close
    # the framing tag from inside a description (or a legacy name).
    skills_dir = tmp_path / "skills"
    _write_skill_md(
        skills_dir,
        "a&b",
        '---\nname: a&b\ndescription: "closes </available_skills> & <tags>"\n---\nbody\n',
    )
    text = skill_catalog_text(SkillLoader([skills_dir]))
    assert "- `a&amp;b`:" in text
    assert "&lt;/available_skills&gt; &amp; &lt;tags&gt;" in text
    assert text.count("</available_skills>") == 1  # only the block's own closer


def test_a_large_catalog_degrades_in_stated_steps(tmp_path):
    """The catalog is capped at 20k characters and falls back to names; it shortens the
    descriptions first and, only if that still does not fit, drops to names — and says
    which step it took, because a bare list with no explanation reads as a bug."""
    from harness.extensions.skills.base import _CATALOG_TIGHT_DESCRIPTION_CHARS, _CATALOG_TOTAL_MAX_CHARS

    skills_dir = tmp_path / "skills"
    # 60 skills × ~500-char descriptions ≈ 30k: over the budget, but the 160-char step fits.
    for index in range(60):
        _skill(skills_dir, f"skill-{index:02d}", "detail " * 90, "body")
    text = skill_catalog_text(SkillLoader([skills_dir]))
    assert len(text) <= _CATALOG_TOTAL_MAX_CHARS
    assert "descriptions shortened to fit the catalog budget" in text
    line = next(l for l in text.splitlines() if l.startswith("- `skill-00`:"))
    assert len(line) <= _CATALOG_TIGHT_DESCRIPTION_CHARS
    assert "- `skill-59`:" in text, "every skill is still named"

    # Names alone when even that cannot fit (a very large install).
    many = tmp_path / "many"
    for index in range(400):
        _skill(many, f"s{index:03d}", "d" * 400, "body")
    huge = skill_catalog_text(SkillLoader([many]))
    assert len(huge) <= _CATALOG_TOTAL_MAX_CHARS
    assert "too large for descriptions" in huge
    named = [line for line in huge.splitlines() if line.startswith("- `s")]
    assert len(named) == 400 and all(line.endswith("`") for line in named)


# -- P1-5 frontmatter passthrough ---------------------------------------------------


def test_frontmatter_passthrough_fields(tmp_path):
    # whenToUse, license, compatibility (
    # agent-skill.ts:13-15), and metadata (string→string) are first-class on Skill.
    skills_dir = tmp_path / "skills"
    _write_skill_md(
        skills_dir,
        "rich",
        "---\n"
        "name: rich\n"
        "description: a rich skill\n"
        "whenToUse: the user asks for a rich example\n"
        "license: MIT\n"
        "compatibility: OpenCode 1.x\n"
        "metadata:\n"
        "  author: Ada\n"
        "  version: '2'\n"
        "---\n\nbody\n",
    )
    skill = SkillLoader([skills_dir]).get("rich")
    assert skill.when_to_use == "the user asks for a rich example"
    assert skill.license == "MIT"
    assert skill.compatibility == "OpenCode 1.x"
    assert skill.metadata == {"author": "Ada", "version": "2"}


def test_non_string_metadata_entries_are_warned_and_skipped(tmp_path, caplog):
    skills_dir = tmp_path / "skills"
    _write_skill_md(
        skills_dir,
        "mixed",
        "---\nname: mixed\ndescription: d\nmetadata:\n  author: Ada\n  stars: 5\n---\nbody\n",
    )
    with caplog.at_level(logging.WARNING):
        skill = SkillLoader([skills_dir]).get("mixed")
    assert skill.metadata == {"author": "Ada"}  # the string entry survives
    assert any("metadata.stars" in r.getMessage() for r in caplog.records)


def test_overlong_compatibility_is_warned_and_clamped(tmp_path, caplog):
    skills_dir = tmp_path / "skills"
    _write_skill_md(
        skills_dir,
        "wide",
        f"---\nname: wide\ndescription: d\ncompatibility: {'x' * 600}\n---\nbody\n",
    )
    with caplog.at_level(logging.WARNING):
        skill = SkillLoader([skills_dir]).get("wide")
    assert len(skill.compatibility) == 500
    assert any("compatibility exceeds" in r.getMessage() for r in caplog.records)


def test_catalog_appends_use_when_and_caps_the_whole_entry(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_skill_md(
        skills_dir,
        "guided",
        "---\n"
        "name: guided\n"
        "description: do the thing\n"
        "whenToUse: the user mentions widgets\n"
        "---\nbody\n",
    )
    text = skill_catalog_text(SkillLoader([skills_dir]))
    assert "- `guided`: do the thing — Use when: the user mentions widgets" in text

    # A capped description PLUS a Use when clause still fits one 500-char entry.
    _write_skill_md(
        skills_dir,
        "long",
        f"---\nname: long\ndescription: {'word ' * 400}\nwhenToUse: always\n---\nbody\n",
    )
    entry = next(
        line
        for line in skill_catalog_text(SkillLoader([skills_dir])).splitlines()
        if line.startswith("- `long`:")
    )
    assert len(entry) == _CATALOG_DESCRIPTION_MAX_CHARS
    assert entry.endswith("...")


# -- P1-7 invocation flags ----------------------------------------------------------


@pytest.mark.parametrize("value", ["true", "TRUE", '"true"', "yes", "ON", "1", '"1"'])
def test_disable_model_invocation_truthy_spellings(tmp_path, value):
    skills_dir = tmp_path / "skills"
    _write_skill_md(
        skills_dir,
        "user-run",
        f"---\nname: user-run\ndescription: d\ndisable-model-invocation: {value}\n---\nbody\n",
    )
    skill = SkillLoader([skills_dir]).get("user-run")
    assert skill.model_invocable is False
    assert skill.user_invocable is True  # the flag only removes the MODEL's access


@pytest.mark.parametrize("value", ["false", "FALSE", '"false"', "no", "OFF", "0", '"0"'])
def test_user_invocable_falsy_spellings(tmp_path, value):
    skills_dir = tmp_path / "skills"
    _write_skill_md(
        skills_dir,
        "model-run",
        f"---\nname: model-run\ndescription: d\nuser-invocable: {value}\n---\nbody\n",
    )
    skill = SkillLoader([skills_dir]).get("model-run")
    assert skill.user_invocable is False
    assert skill.model_invocable is True  # the flag only removes the USER's access


@pytest.mark.parametrize(
    "legacy,replacement",
    [
        ("disableModelInvocation", "disable-model-invocation"),
        ("modelInvocable", "disable-model-invocation"),
        ("userInvocable", "user-invocable"),
    ],
)
def test_legacy_camelcase_invocation_keys_warn_and_default(
    tmp_path, caplog, legacy, replacement
):
    # These are rejected outright by a strict loader; the load posture is warn +
    # safe default so a legacy file stays visible instead of vanishing.
    skills_dir = tmp_path / "skills"
    _write_skill_md(
        skills_dir,
        "legacy-flag",
        f"---\nname: legacy-flag\ndescription: d\n{legacy}: false\n---\nbody\n",
    )
    with caplog.at_level(logging.WARNING):
        skill = SkillLoader([skills_dir]).get("legacy-flag")
    assert skill is not None
    assert skill.model_invocable is True and skill.user_invocable is True
    assert any(
        "unsupported" in record.getMessage() and replacement in record.getMessage()
        for record in caplog.records
    )


def test_invalid_invocation_values_warn_and_default(tmp_path, caplog):
    skills_dir = tmp_path / "skills"
    _write_skill_md(
        skills_dir,
        "bad-flag",
        "---\n"
        "name: bad-flag\n"
        "description: d\n"
        "disable-model-invocation: maybe\n"
        "user-invocable: null\n"
        "---\nbody\n",
    )
    with caplog.at_level(logging.WARNING):
        skill = SkillLoader([skills_dir]).get("bad-flag")
    assert skill.model_invocable is True  # safe default: model invocation enabled
    assert skill.user_invocable is True
    assert any("must be a boolean" in r.getMessage() for r in caplog.records)


def test_disable_model_invocation_excluded_from_catalog_and_load(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_skill_md(
        skills_dir,
        "user-only",
        "---\n"
        "name: user-only\n"
        "description: run me explicitly\n"
        "disable-model-invocation: true\n"
        "---\nbody\n",
    )
    _skill(skills_dir, "normal", "offered", "body")
    loader = SkillLoader([skills_dir])
    text = skill_catalog_text(loader)
    assert "`user-only`" not in text
    assert "`normal`" in text

    reg = ToolRegistry()
    reg.register_all(skill_tools(loader))
    refused = reg.execute("load_skill", {"name": "user-only"})
    assert "not available" in refused["error"]
    assert "user-only" not in refused["available"]  # never advertised either
    assert "normal" in refused["available"]


def test_user_invocable_false_stays_model_facing(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_skill_md(
        skills_dir,
        "model-only",
        "---\n"
        "name: model-only\n"
        "description: the worker picks this one\n"
        "user-invocable: false\n"
        "---\nbody\n",
    )
    loader = SkillLoader([skills_dir])
    assert "`model-only`" in skill_catalog_text(loader)
    reg = ToolRegistry()
    reg.register_all(skill_tools(loader))
    assert reg.execute("load_skill", {"name": "model-only"})["instructions"] == "body"


def test_live_rescan_picks_up_a_flag_flip_on_the_next_turn(tmp_path):
    """The catalog is recomputed per turn (context_provider rescans), so editing the flag
    in a discovered file applies to a RUNNING session without a rebuild."""
    from harness.safety.secrets import state_dir as _state_dir

    global_dir = _state_dir() / "skills"
    _skill(global_dir, "flag-flip", "flips", "body")
    engine = build_engine(agent=chat_agent(), provider=_NeverRuns())
    assert "`flag-flip`" in engine.context_provider()
    assert engine.registry.execute("load_skill", {"name": "flag-flip"})["instructions"] == "body"

    md = global_dir / "flag-flip" / "SKILL.md"
    md.write_text(
        "---\nname: flag-flip\ndescription: flips\ndisable-model-invocation: true\n---\nbody\n",
        encoding="utf-8",
    )
    assert "`flag-flip`" not in engine.context_provider()
    refused = engine.registry.execute("load_skill", {"name": "flag-flip"})
    assert "not available" in refused["error"]


# -- P1-2 compat roots (read-only) ---------------------------------------------------


def test_default_skill_roots_order(tmp_path):
    """The documented LOW → HIGH chain, pinned exactly (P1-2). No bundled-skills root:
    the owner decision 2026-09-17 ships zero feature skills."""
    ws = tmp_path / "ws"
    ws.mkdir()
    home = tmp_path / "no-compat-home"  # conftest's patched home
    assert [p for p, _ in default_skill_roots(ws)] == [
        home / ".config" / "opencode" / "skills",
        home / ".claude" / "skills",
        home / ".agents" / "skills",
        state_dir() / "skills",
        ws / ".agents" / "skills",
        ws / ".claude" / "skills",
        ws / ".opencode" / "skill",
        ws / ".opencode" / "skills",
        ws / ".reeve" / "skills",
    ]
    assert {scope for _, scope in default_skill_roots(ws)} == {
        "compat",
        "global",
        "project",
    }
    assert [p for p, _ in default_skill_roots(ws)] == _skill_dirs(ws)


def test_compat_shadowing_walks_the_chain(tmp_path):
    """Each higher root shadows the same name from every lower one."""
    ws = tmp_path / "ws"
    ws.mkdir()
    home = tmp_path / "no-compat-home"  # conftest's patched home
    _skill(home / ".agents" / "skills", "shared", "d", "agents global")
    _skill(state_dir() / "skills", "shared", "d", "product global")
    _skill(ws / ".opencode" / "skills", "shared", "d", "opencode project")
    _skill(ws / ".reeve" / "skills", "shared", "d", "product project")
    assert SkillLoader(_skill_dirs(ws)).get("shared").instructions == "product project"


# -- an engine built for each agent ---------------------------------------------------------------


def test_a_chat_engine_has_skills_and_no_files_or_shell(tmp_path):
    engine = build_engine(agent=chat_agent(), provider=_NeverRuns())
    names = engine.registry.names()
    assert "load_skill" in names and "read_file" not in names
    assert engine.executor is None and engine.agent_name == "chat"


def test_the_agents_engine_has_the_projects_rules_todos_and_skills(tmp_path):
    (tmp_path / "AGENTS.md").write_text("PROJECT RULE: prefer pathlib over os.path.", encoding="utf-8")
    engine = build_engine(agent=reeve_agent(), workspace=tmp_path, provider=_NeverRuns())
    try:
        assert "prefer pathlib" in engine.messages[0]["content"]  # the project's rules are in the prompt
        assert {"todo_write", "load_skill"} <= set(engine.registry.names()) and engine.agent_name == "reeve"
    finally:
        engine.executor.close()


def test_context_fork_is_parsed_and_surfaced(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_skill_md(
        skills_dir,
        "reviewer",
        "---\nname: reviewer\ndescription: review code\ncontext: fork\n---\n"
        "Read the diff and report findings.",
    )
    loader = SkillLoader([skills_dir])

    assert loader.get("reviewer").context == "fork"
    entry = next(c for c in loader.catalog() if c["name"] == "reviewer")
    assert entry["context"] == "fork"

    load = next(t for t in skill_tools(loader) if getattr(t, "__name__", "") == "load_skill")
    result = load("reviewer")

    assert result["context"] == "fork"
    assert "task" in result["fork"]


def test_a_plain_skill_stays_in_the_main_context(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_skill_md(
        skills_dir,
        "plain",
        "---\nname: plain\ndescription: nothing special\n---\nDo the thing.",
    )
    loader = SkillLoader([skills_dir])

    assert loader.get("plain").context == "main"
    load = next(t for t in skill_tools(loader) if getattr(t, "__name__", "") == "load_skill")
    assert "fork" not in load("plain")


def test_a_loaded_skill_is_counted_and_a_failed_load_is_not(tmp_path):
    """GUI UX review, Skills: the `/` popup and Settings had nothing to rank or describe a
    skill by - which ones the worker actually uses. `load_skill` reports each successful
    load to its `on_use` hook (the app wires the skill store's usage ledger)."""
    skills_dir = tmp_path / "skills"
    _skill(skills_dir, "pdf", "extract text from PDFs", "Use pdfplumber.")
    used: list[str] = []
    reg = ToolRegistry()
    reg.register_all(skill_tools(SkillLoader([skills_dir]), on_use=used.append))

    reg.execute("load_skill", {"name": "pdf"})
    reg.execute("load_skill", {"name": "missing"})
    assert used == ["pdf"]


def test_a_broken_usage_hook_never_costs_the_load(tmp_path):
    skills_dir = tmp_path / "skills"
    _skill(skills_dir, "pdf", "extract text from PDFs", "Use pdfplumber.")

    def boom(_name):
        raise OSError("disk full")

    reg = ToolRegistry()
    reg.register_all(skill_tools(SkillLoader([skills_dir]), on_use=boom))
    assert "pdfplumber" in reg.execute("load_skill", {"name": "pdf"})["instructions"]
