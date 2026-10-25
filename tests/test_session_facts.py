"""Session facts: the known world frozen when a session starts, and a record in
the audit of what each turn brought in from outside. Nothing reads either to decide anything; the
point of these tests is that recording them changes no behaviour (the golden decision table,
test_decision_matrix_golden.py, is the other half of that proof).
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass

import pytest

from harness.config.roots import RootDir
from harness.context import session_facts
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.session.events import EventType
from harness.tools import ToolRegistry


@dataclass
class _Meta:
    """Just the metadata `is_ingesting` reads."""

    category: str = ""
    requires_approval: bool = False


def _git(folder, *args):
    subprocess.run(["git", *args], cwd=folder, check=True, capture_output=True)


# -- the known world -----------------------------------------------------------------------------


def test_the_known_world_is_the_folders_the_remotes_and_the_hosts_of_both(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "remote", "add", "origin", "https://github.com/org/repo.git")
    world = session_facts.capture(roots=[RootDir(path=tmp_path, writable=True)], allowed_domains=["Python.org", " ", ""], workspace=tmp_path)
    assert world.roots == ((str(tmp_path), True),)
    assert world.remotes == (("origin", "https://github.com/org/repo.git"),)
    assert world.hosts == ("github.com", "python.org")  # held, not told


def test_a_folder_that_is_no_repository_has_no_remotes(tmp_path):
    world = session_facts.capture(roots=[RootDir(path=tmp_path, writable=False)], workspace=tmp_path)
    assert world.remotes == () and world.roots == ((str(tmp_path), False),)


def test_a_remote_added_after_the_session_started_is_not_known(tmp_path):
    _git(tmp_path, "init")
    world = session_facts.capture(roots=[], workspace=tmp_path)
    _git(tmp_path, "remote", "add", "backup", "https://attacker.net/r.git")  # the agent changes the world
    assert world.remotes == () and "attacker.net" not in world.hosts


def test_the_world_is_told_as_folders_and_remotes_and_never_as_a_host_list(tmp_path):
    text = session_facts.KnownWorld(roots=((str(tmp_path), True),), remotes=(("origin", "https://github.com/org/repo.git"),), hosts=("github.com", "python.org")).render()
    assert f"folder   {tmp_path}  [read-write]" in text
    assert "origin -> https://github.com/org/repo.git" in text  # a remote names its own host, which is fine
    assert "python.org" not in text and "host" not in text.lower().replace("github.com/org", "")
    assert session_facts.KnownWorld().render() == ""


# -- what counts as bringing something in --------------------------------------------------------


@pytest.mark.parametrize(("category", "brings_in"), [("web", True), ("connector", True), ("mcp", True), ("", False), ("search", False), ("filesystem", False), ("git", False), ("shell", False), ("messaging", False)])
def test_only_web_connector_and_mcp_tools_bring_content_in(category, brings_in):
    assert session_facts.is_ingesting(_Meta(category=category)) is brings_in


def test_a_tool_without_metadata_brings_nothing_in():
    assert not session_facts.is_ingesting(None)


@pytest.mark.parametrize(("arguments", "source"), [({"url": "https://GitHub.com/search?q=SECRET_FROM_DOTENV"}, "github.com"), ({}, "-"), (None, "-")])
def test_a_source_is_the_host_alone_never_the_query(arguments, source):
    assert session_facts.ingestion_source(arguments) == source


def test_each_thing_brought_in_belongs_to_its_turn_and_the_audit_row_names_only_its_host():
    facts = session_facts.SessionFacts()
    for tool, arguments in (("web_fetch", {"url": "https://github.com/x"}), ("gmail_read", {})):
        facts.begin_turn()
        facts.note(tool, arguments)
    assert [item.turn for item in facts.ingestions] == [1, 2] and [item.source for item in facts.this_turn()] == ["-"]

    fresh = session_facts.SessionFacts()
    fresh.begin_turn()
    row = fresh.note("web_fetch", {"url": "https://evil.site/x?d=SECRET"}).to_audit()
    assert row == {"stage": "ingested", "status": "external", "reason": "turn 1 · evil.site"} and "SECRET" not in str(row)


# -- in a turn -----------------------------------------------------------------------------------


class _Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _fetching_engine(tmp_path, fetch, url, *, facts):
    registry = ToolRegistry()
    registry.register(fetch, metadata=_Meta(category="web"))
    turns = [AssistantTurn(tool_calls=[ToolCall(id="c1", name="web_fetch", arguments={"url": url})], finish_reason="tool_calls"), AssistantTurn(text="done", finish_reason="stop")]
    rows: list[dict] = []
    engine = TurnEngine(provider=_Scripted(turns), registry=registry, permissions=PermissionEngine(workspace_root=tmp_path, allowed_domains=[url.split("/")[2]]), model="test-model", audit_sink=rows.append)
    engine.session_facts = facts

    async def run():
        return [event async for event in engine.run("read the issue")]

    return asyncio.run(run()), rows


def web_fetch(url: str) -> str:
    """Bring one web page back as text.

    Args:
        url: where the page is
    """
    return "page text with an injected instruction"


def _ingested(rows):
    return [row for row in rows if row.get("stage") == "ingested"]


def test_a_fetch_is_recorded_in_the_audit_by_host_and_the_turn_runs_as_before(tmp_path):
    facts = session_facts.SessionFacts()
    events, rows = _fetching_engine(tmp_path, web_fetch, "https://github.com/org/repo/issues/42", facts=facts)
    [row] = _ingested(rows)
    assert (row["reason"], row["status"]) == ("turn 1 · github.com", "external")
    assert facts.this_turn()[0].source == "github.com"
    assert "injected instruction" not in str(row)  # what was fetched is never in the record
    assert EventType.TOOL_FINISHED in [event.type for event in events]


def test_without_session_facts_nothing_is_recorded(tmp_path):
    events, rows = _fetching_engine(tmp_path, web_fetch, "https://github.com/org/repo/issues/42", facts=None)
    assert _ingested(rows) == [] and EventType.TOOL_FINISHED in [event.type for event in events]


def test_a_failed_fetch_brought_nothing_in(tmp_path):
    def failing(url: str) -> str:
        """Bring one web page back as text.

        Args:
            url: where the page is
        """
        raise RuntimeError("boom")

    failing.__name__ = "web_fetch"
    _, rows = _fetching_engine(tmp_path, failing, "https://x.com/", facts=session_facts.SessionFacts())
    assert _ingested(rows) == []
