"""Which skills a conversation has: every scope merged, less what Settings turned
off, less what this conversation muted.

One resolver (`effective_skills`, `manager.effective_skill_names`) feeds the engine's menu, the
side panel and the composer's list, so they cannot disagree. Off wins: a conversation cannot switch
back on what Settings turned off.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.extensions.skills import SessionSkillStore, SkillLoader, effective_skills, skill_catalog_text, skill_tools
from harness.interface.server.manager import SessionManager
from harness.llm.providers import ModelCapabilities, ProviderClient


class _NoModel(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):  # pragma: no cover
        raise AssertionError("no turn is run")

    def capabilities(self, model):
        return ModelCapabilities()


def _skill(base: Path, name: str, description: str = "when it applies", body: str = "do it") -> None:
    (base / name).mkdir(parents=True)
    (base / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n", encoding="utf-8")


@pytest.fixture()
def manager(tmp_path):
    return SessionManager(workspace=tmp_path / "ws", provider=_NoModel())


def _engine(manager):
    from harness.compose import build_engine
    from harness.modes.agents.chat import chat_agent

    # An agent without a workspace: the persona is gone, its builder is not.
    return build_engine(agent=chat_agent(), provider=_NoModel(), skill_filter=lambda: manager.effective_skill_names("s1"))


def _load(engine, name):
    return engine.registry.execute("load_skill", {"name": name})


# -- the resolver --------------------------------------------------------------------------------


def test_the_project_copy_of_a_skill_wins_over_the_global_one(tmp_path):
    _skill(tmp_path / "g", "report", body="generic steps")
    _skill(tmp_path / "p", "report", body="project steps")
    assert SkillLoader([tmp_path / "g", tmp_path / "p"]).get("report").instructions == "project steps"  # nearest last


@pytest.mark.parametrize(
    ("disabled", "muted", "left"),
    [
        ({"a"}, {}, {"b"}),  # off in Settings
        (set(), {"a": False}, {"b"}),  # muted here
        (set(), {"ghost": False}, {"a", "b"}),  # muting a skill nobody has changes nothing
        ({"a"}, {"a": True}, {"b"}),  # a conversation cannot switch back on what Settings turned off
    ],
)
def test_a_skill_is_offered_unless_settings_or_this_conversation_turned_it_off(disabled, muted, left):
    assert effective_skills(names={"a", "b"}, disabled=disabled, session_overrides=muted) == left


def test_a_mute_holds_in_its_own_conversation_only_survives_a_restart_and_goes_with_it(tmp_path):
    store = SessionSkillStore(tmp_path / "s.json")
    store.set("s1", "a", False)
    store.set("s2", "b", False)
    assert store.get("s1") == {"a": False} and store.get("s2") == {"b": False}
    assert effective_skills(names={"a", "b"}, disabled=set(), session_overrides=store.get("s3")) == {"a", "b"}
    assert SessionSkillStore(tmp_path / "s.json").get("s1") == {"a": False}
    store.remove_session("s1")
    assert store.get("s1") == {}


# -- the manager ---------------------------------------------------------------------------------


def test_without_a_workspace_only_global_skills_are_offered(manager, tmp_path):
    _skill(manager.skill_store.global_dir, "everywhere")
    workspace = tmp_path / "elsewhere"
    _skill(workspace / ".reeve" / "skills", "local-only")
    assert manager.effective_skill_names("s1") == {"everywhere"}
    assert manager.effective_skill_names("s1", workspace) == {"everywhere", "local-only"}


def test_a_workspace_with_no_skills_folder_offers_nothing_and_breaks_nothing(manager, tmp_path):
    bare = tmp_path / "bare-ws"
    bare.mkdir()
    assert manager.effective_skill_names("s1", bare) == set()


def test_with_no_skills_the_menu_is_empty_and_loading_one_names_what_there_is(tmp_path):
    from harness.tools.registry import ToolRegistry

    loader = SkillLoader([tmp_path / "nowhere"])
    assert skill_catalog_text(loader) == ""
    registry = ToolRegistry()
    registry.register_all(skill_tools(loader))
    refused = registry.execute("load_skill", {"name": "ghost"})
    assert refused["error"].startswith("unknown skill") and refused["available"] == []


def test_the_side_panel_and_the_models_menu_come_from_one_answer(manager):
    """What is on in the panel is what the model is offered."""
    for name in ("alpha", "beta", "gamma"):
        _skill(manager.skill_store.global_dir, name)
    manager.skill_store.set_enabled("beta", False)  # off in Settings: in neither
    manager.session_skills.set("s1", "gamma", False)  # muted here: listed, switched off
    offered = manager.effective_skill_names("s1")
    rows = manager.session_skills_view("s1")["skills"]
    assert offered == {"alpha"}
    assert {r["name"] for r in rows} == {"alpha", "gamma"} and {r["name"] for r in rows if r["enabled"]} == offered


# -- what the model sees -------------------------------------------------------------------------


def test_what_the_model_is_offered_follows_the_skills_as_they_are_now(manager):
    """Nothing the model is offered is fixed when the session starts:
    the menu is built for each turn, so a skill added or switched off mid-session counts from the
    next message (decided 2026-07-27), and `load_skill` looks at the skills as they are when it is
    called. To the model a deleted skill and a switched-off one are the same. What a conversation
    already loaded stays in its history, and that is all that stays."""
    _skill(manager.skill_store.global_dir, "early", body="early body")
    engine = _engine(manager)

    def menu():
        return engine.context_provider()

    assert "early" in menu() and "Available skills" not in engine.messages[0]["content"]  # in the turn's block, not the prompt
    manager.create_skill({"name": "late", "description": "d", "instructions": "late body"})
    assert "late" in menu() and _load(engine, "late")["instructions"] == "late body"  # made after the engine was
    manager.skill_store.set_enabled("early", False)
    refused = _load(engine, "early")
    assert "early" not in menu() and refused["error"].startswith("unknown skill") and "early" not in refused["available"]
    manager.delete_skill("late")
    assert "late" not in menu() and _load(engine, "late")["error"].startswith("unknown skill")
    manager.skill_store.set_enabled("early", True)  # off only parks it: its files were never touched
    assert "early" in menu() and _load(engine, "early")["instructions"] == "early body"


def test_a_skill_without_a_description_is_never_offered_but_can_be_loaded(manager):
    _skill(manager.skill_store.global_dir, "quiet", description="", body="quiet body")
    _skill(manager.skill_store.global_dir, "loud", description="offered loudly")
    engine = _engine(manager)
    menu = engine.context_provider()
    assert "`loud`" in menu and "`quiet`" not in menu
    assert _load(engine, "quiet")["instructions"] == "quiet body"


def test_a_loaded_skill_that_is_turned_off_is_countermanded_each_turn_until_it_is_back(manager):
    """A shorter menu says nothing about instructions already in the conversation, so a skill
    that was loaded and then turned off or deleted gets a stop note; one never loaded does not."""
    _skill(manager.skill_store.global_dir, "used-one", body="used body")
    _skill(manager.skill_store.global_dir, "unused-one", body="never loaded")
    engine = _engine(manager)
    engine.messages += [  # loaded earlier in this conversation
        {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "load_skill", "arguments": json.dumps({"name": "used-one"})}}]},
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"name": "used-one", "instructions": "used body", "resources_path": "x"})},
    ]
    assert "disabled by the user" not in engine.context_provider()
    manager.skill_store.set_enabled("used-one", False)
    context = engine.context_provider()
    assert 'skill "used-one" has been disabled' in context
    assert "`used-one`" not in context and "`unused-one`" in context  # no note for one never loaded
    manager.skill_store.set_enabled("used-one", True)
    assert "disabled by the user" not in engine.context_provider()
    manager.delete_skill("used-one")  # deleting counts as turning it off
    assert 'skill "used-one" has been disabled' in engine.context_provider()


def test_a_skill_can_carry_a_line_for_people_that_the_model_never_sees(tmp_path):
    """The "/" menu showed each skill's description, which is written for the model ("Use this
    skill whenever the person asks…") and was cut mid-sentence (owner, 2026-10-13). A skill's
    `summary:` is the line for people; it is not in the model's catalog."""
    folder = tmp_path / "g" / "release-notes"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: release-notes\ndescription: Use this skill whenever the person asks for release notes\nsummary: Draft release notes from the git log\n---\n\nsteps\n", encoding="utf-8")
    loader = SkillLoader([tmp_path / "g"])
    assert loader.get("release-notes").summary == "Draft release notes from the git log"
    assert all("summary" not in row for row in loader.catalog())
    assert "Draft release notes" not in skill_catalog_text(loader)
