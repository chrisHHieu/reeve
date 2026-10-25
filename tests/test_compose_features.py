"""A session is assembled from features: each one file, run in order, and a feature the
config removes leaves nothing behind (its tools, its approval extra, its interrupt hook, the value
it provides), while the core is no feature and cannot be removed."""

from __future__ import annotations

import logging
import re

from harness.compose import build_engine
from harness.compose.features import FEATURE_IDS, FEATURES, FeatureContext, SessionInputs, removed_ids
from harness.config.config import Config
from harness.modes.agents.reeve import reeve_agent


def _engine(tmp_path, remove=None, **kwargs):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    if remove is not None:
        (ws / ".reeve").mkdir(exist_ok=True)
        names = ", ".join(f'"{name}"' for name in remove)
        (ws / ".reeve" / "config.toml").write_text(f"[features]\nremove = [{names}]\n", encoding="utf-8")
    return build_engine(agent=reeve_agent(), workspace=ws, model="openai:gpt-5.6-sol", session_id="s1", **kwargs)


def _names(engine) -> set[str]:
    return {schema["function"]["name"] for schema in engine.registry.all_schemas()}


def test_feature_ids_are_unique_and_the_order_holds_what_depends_on_it():
    assert len(set(FEATURE_IDS)) == len(FEATURE_IDS)
    # `workflow` is offered only where `task` exists, so code mode runs after delegation; discovery
    # defers from every tool the others registered, so it is last.
    assert FEATURE_IDS.index("code_mode") > FEATURE_IDS.index("delegation")
    assert FEATURE_IDS[-1] == "discovery"
    # The core is not in the list, so no config can name it.
    assert not {"permissions", "compaction", "memory_gate", "session_log", "files", "shell"} & set(FEATURE_IDS)


def test_removing_web_takes_its_tools_and_its_approval_card_extra(tmp_path):
    kept = _engine(tmp_path / "a")
    assert {"web_search", "web_fetch"} <= _names(kept)
    assert "search_provider" in kept.approval_extras("web_search", {})

    removed = _engine(tmp_path / "b", remove=["web"])
    assert not {"web_search", "web_fetch"} & _names(removed)
    assert removed.approval_extras("web_search", {}) == {}
    # Only the web went: the rest of the session is as it was.
    assert _names(kept) - _names(removed) == {"web_search", "web_fetch"}


def test_removing_delegation_takes_task_workflow_and_its_stop_hook(tmp_path):
    kept = _engine(tmp_path / "a")
    assert {"task", "workflow", "run_code"} <= _names(kept)
    assert len(kept._interrupt_hooks) == 2  # the shell, then the subagents' stop flag

    removed = _engine(tmp_path / "b", remove=["delegation"])
    names = _names(removed)
    assert "task" not in names
    # Code mode stays, and offers no `workflow` without subagents to start.
    assert "run_code" in names and "workflow" not in names
    assert len(removed._interrupt_hooks) == 1


def test_removing_skills_takes_its_tools_and_the_loader_it_provides(tmp_path):
    kept = _engine(tmp_path / "a")
    assert {"load_skill", "save_skill"} <= _names(kept)
    assert kept.skill_loader is not None

    removed = _engine(tmp_path / "b", remove=["skills"])
    assert not {"load_skill", "save_skill"} & _names(removed)
    assert removed.skill_loader is None
    # The live context still renders, without a skill menu.
    assert removed.context_provider().startswith("Now: ")


def test_a_name_that_is_no_feature_is_logged_and_ignored(caplog):
    config = Config()
    config.features = {"remove": ["webb", "web", "permissions"]}
    with caplog.at_level(logging.WARNING):
        assert removed_ids(config) == {"web"}
    assert any("webb" in record.message and "permissions" in record.message for record in caplog.records)
    config.features = {"remove": "lsp"}
    assert removed_ids(config) == {"lsp"}
    config.features = {}
    assert removed_ids(config) == set()


def test_an_opt_in_feature_is_off_until_the_persons_config_adds_it(tmp_path):
    """`request_tool` was ~295 tokens of every request and was never called in 860 sessions
    (2026-10-24): it is off until `[features] add` names it, and only the person's config can."""
    from harness.config.config import global_config_path

    assert "request_tool" not in _names(_engine(tmp_path / "default"))
    # A cloned repository cannot turn it on.
    ws = tmp_path / "repo" / "ws"
    (ws / ".reeve").mkdir(parents=True)
    (ws / ".reeve" / "config.toml").write_text('[features]\nadd = ["tool_request"]\n', encoding="utf-8")
    assert "request_tool" not in _names(_engine(tmp_path / "repo"))
    global_config_path().parent.mkdir(parents=True, exist_ok=True)
    global_config_path().write_text('[features]\nadd = ["tool_request"]\n', encoding="utf-8")
    assert "request_tool" in _names(_engine(tmp_path / "added"))
    # `remove` wins over `add`, wherever it is written.
    assert "request_tool" not in _names(_engine(tmp_path / "both", remove=["tool_request"]))


def test_a_workspace_remove_joins_the_persons_and_cannot_undo_it(tmp_path):
    """The workspace's `[features]` table used to replace the person's whole, so a repository
    writing `remove = []` brought back the web the person had removed."""
    from harness.config.config import global_config_path

    global_config_path().parent.mkdir(parents=True, exist_ok=True)
    global_config_path().write_text('[features]\nremove = ["web"]\n', encoding="utf-8")
    names = _names(_engine(tmp_path / "undo", remove=[]))
    assert not {"web_search", "web_fetch"} & names
    both = _names(_engine(tmp_path / "join", remove=["lsp"]))
    assert not {"web_search", "web_fetch", "lsp"} & both


def test_prompt_sections_join_in_their_own_order_not_the_order_features_ran(tmp_path):
    ctx = FeatureContext(
        inputs=SessionInputs(agent=None), agent=None, config=None, registry=None, permissions=None,
        secrets=None, provider=None, model="", model_settings=None, world=None, executor=None,
        context=None, todo=None, root_list=[], ws=None, remote=False, remote_root="", memory_root=None,
        spill=None, undo=None, intents=None, plan_path=None, witnesses=None,
        edit_exempt_roots=lambda: [], held_edit_tools={},
    )
    seen = []
    ctx.prompt(20, lambda: "recap")
    ctx.prompt(10, lambda: seen.append("rendered") or "memory")
    # Sections render only when asked, once every feature has run and the tool list is final.
    assert seen == []
    assert ctx.render_prompt_sections() == ["memory", "recap"]


def _prompt(engine) -> str:
    return engine.messages[0]["content"]


def _named(prompt: str, tool: str) -> bool:
    """Whether the prompt names `tool` as a tool: a snake_case name anywhere, a plain word only in
    the forms the prompt uses for one (`(task)`, `the lsp tool`, `lsp diagnostics`)."""
    if "_" in tool:
        return re.search(rf"(?<![A-Za-z0-9_]){re.escape(tool)}(?![A-Za-z0-9_])", prompt) is not None
    return any(form in prompt for form in (f"({tool})", f"the {tool} tool", f"{tool} diagnostics", f"{tool} for a large"))


def test_a_removed_feature_leaves_no_word_in_the_prompt_that_names_its_tools(tmp_path):
    full = _engine(tmp_path / "full", question_asker=object())
    full_prompt, full_names = _prompt(full), _names(full)
    for index, feature in enumerate(("web", "delegation", "skills", "lsp", "deliverables", "ask_user", "code_mode", "design")):
        # A folder named for the feature would put the tool's name in the workspace path.
        engine = _engine(tmp_path / f"case{index}", remove=[feature], question_asker=object())
        gone = full_names - _names(engine)
        assert gone, feature
        mentioned = {tool for tool in gone if _named(full_prompt, tool)}
        still = {tool for tool in mentioned if _named(_prompt(engine), tool)}
        assert still == set(), (feature, still)
    # Every tool the full prompt names is held, and the prompt with everything present is the
    # agent's prompt as written, byte for byte.
    assert reeve_agent().prompt_for(full_names) == reeve_agent().system_prompt
    assert reeve_agent().system_prompt in full_prompt


def test_the_prompt_itself_says_where_a_design_goes_without_the_guide(tmp_path):
    # design_guide is a cold tool a model may never fetch (a real session did not, 2026-10-19):
    # the folder the check acts on and the variant names must be in the prompt as sent.
    prompt = _prompt(_engine(tmp_path / "with"))
    assert "under `design/` at the project root" in prompt and "`design/pricing.a.html`" in prompt
    assert "take no screenshots of your own" in prompt
    assert "`design/`" not in _prompt(_engine(tmp_path / "without", remove=["design"]))


def test_the_web_line_goes_and_the_sentence_around_it_still_reads(tmp_path):
    prompt = _prompt(_engine(tmp_path, remove=["web"]))
    assert "web_search" not in prompt and "across the web" not in prompt
    assert "workspace (files, shell, git).\n\n# Communication" in prompt
    assert "the search tools for files. read_file also shows" in prompt


def test_the_memory_section_names_only_the_tools_held():
    from pathlib import Path

    from harness.context.memory.prompt import memory_section

    every = memory_section(Path("mem"))
    assert "`run_shell` and `run_code` ask)" in every and "`apply_patch`" in every
    assert memory_section(Path("mem"), tools={"write_file", "run_shell", "run_code", "apply_patch"}) == every
    no_code = memory_section(Path("mem"), tools={"write_file", "run_shell"})
    assert "`run_shell` asks)" in no_code and "run_code" not in no_code and "apply_patch" not in no_code
    bare = memory_section(Path("mem"), tools={"write_file"})
    assert "(reading the directory runs without asking)" in bare and "run_shell" not in bare


def test_every_feature_has_a_setup_and_an_id():
    for feature in FEATURES:
        assert isinstance(feature.id, str) and feature.id
        assert callable(feature.setup)
