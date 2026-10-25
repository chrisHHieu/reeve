"""Memory over HTTP and in a session: the switches and the person's instructions, what a
session is given with learning on and off, and the two memory folders as files."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from harness.context.memory.settings import MAX_USER_RULES_CHARS
from harness.interface.server import SessionManager, create_app
from harness.llm.providers import ModelCapabilities, ProviderClient


class _NeverRuns(ProviderClient):
    """The engines here are built and looked at, never run."""

    def complete(self, **request):  # pragma: no cover
        raise AssertionError("no turn is run")

    def capabilities(self, model):
        return ModelCapabilities(tools=True)


def _served(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=_NeverRuns())
    return TestClient(create_app(manager)), manager


def _remember(manager, text: str) -> None:
    manager._user_memory().save_memory(title=text, description=text, note_type="user", body=text)


# -- the switches ---------------------------------------------------------------------------------


def test_each_memory_setting_changes_alone_and_is_read_back(tmp_path):
    client, _ = _served(tmp_path)

    def settings():
        return client.get("/v1/memory/settings").json()

    def put(**changed):
        reply = client.put("/v1/memory/settings", json=changed).json()
        return reply["enabled"], reply["user_rules"], reply["auto_memory"]

    fresh = settings()
    assert (fresh["enabled"], fresh["user_rules"], fresh["auto_memory"]) == (True, "", True)
    # each field changes alone, and the others stay as they were
    assert put(user_rules="Reply in Hindi") == (True, "Reply in Hindi", True)
    assert put(enabled=False) == (False, "Reply in Hindi", True)
    assert put(auto_memory=False)[2] is False and settings()["auto_memory"] is False


def test_the_persons_instructions_are_cut_to_their_limit_by_the_server(tmp_path):
    # a client that means harm, or has a bug, cannot grow every system prompt after it
    client, _ = _served(tmp_path)
    client.put("/v1/memory/settings", json={"user_rules": "r" * (MAX_USER_RULES_CHARS + 9_000)})
    kept = client.get("/v1/memory/settings").json()["user_rules"]
    assert len(kept) == MAX_USER_RULES_CHARS


# -- what a session is given -----------------------------------------------------------


def test_the_persons_instructions_reach_a_session_and_nothing_else_about_them_does(tmp_path):
    """What holds in every project is the file the person wrote. What a background pass
    once kept about them is still listed in Settings for them to look at or delete; no session loads
    it, and none can write there."""
    client, manager = _served(tmp_path)
    rule = "Always reply in Hindi"
    client.put("/v1/memory/settings", json={"user_rules": rule})
    _remember(manager, "a figure nobody asked the model to keep")
    engine = manager.get_engine("rules-session")
    prompt = engine.messages[0]["content"]
    assert rule in prompt and "outrank" in prompt  # with the rule that it comes first
    assert "a figure nobody asked" not in prompt
    assert all("about you" not in root.label for root in engine.roots)
    kept = [f["name"] for f in client.get("/v1/memory/auto", params={"scope": "user"}).json()["files"]]
    assert any(name.startswith("a-figure-nobody") for name in kept)


# -- the project directory ----------------------------------------------------------------


def test_auto_memory_files_journey(tmp_path):
    client, _ = _served(tmp_path)
    ws = str(tmp_path)

    assert client.get("/v1/memory/auto", params={"workspace": ws}).json()["files"] == []

    written = client.put(
        "/v1/memory/auto/file",
        json={
            "workspace": ws,
            "name": "feedback_testing.md",
            "content": "run pytest -q",
            "mode": "overwrite",
        },
    ).json()
    assert written["ok"] is True

    listed = client.get("/v1/memory/auto", params={"workspace": ws}).json()
    assert [f["name"] for f in listed["files"]] == ["feedback_testing.md"]
    # Machine-local: under a projects/ dir keyed by the project, NOT inside the workspace.
    assert "projects" in listed["dir"]
    assert Path(listed["dir"], "feedback_testing.md").exists()

    read = client.get(
        "/v1/memory/auto/file",
        params={"workspace": ws, "name": "feedback_testing.md"},
    ).json()
    assert "run pytest -q" in read["content"]
    assert "type:" in read["content"]  # topic frontmatter stamped on write

    assert client.delete(
        "/v1/memory/auto/file",
        params={"workspace": ws, "name": "feedback_testing.md"},
    ).json()["ok"] is True
    assert client.get("/v1/memory/auto", params={"workspace": ws}).json()["files"] == []


def test_auto_memory_setting_toggles(tmp_path):
    client, _ = _served(tmp_path)
    assert client.get("/v1/memory/settings").json()["auto_memory"] is True
    out = client.put("/v1/memory/settings", json={"auto_memory": False}).json()
    assert out["auto_memory"] is False
