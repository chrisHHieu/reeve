"""Skills over the API: the settings page's routes, a skill muted for one
conversation, the menu the engine offers, and a skill run by name from the composer.

Like every other /v1 management route, a refusal is a `{"ok": False, "error": …}` body, not a 4xx.
The model is scripted and keeps what it was sent.
"""

from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness.interface.server import SessionManager, create_app
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient

GREET = {"name": "greet", "description": "says hello", "instructions": "Say hello warmly."}


class ScriptedProvider(ProviderClient):
    """Answers with the queued turns and keeps every request's messages."""

    def __init__(self, turns=None):
        self._turns = list(turns or [])
        self.seen: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.seen.append(messages)
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _client(tmp_path, turns=None):
    """The app over a manager whose model is scripted, with both, and the model."""
    model = ScriptedProvider(turns)
    manager = SessionManager(workspace=tmp_path, provider=model)
    return TestClient(create_app(manager)), manager, model


def _zip_b64(entries: dict[str, str]) -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return base64.b64encode(buffer.getvalue()).decode()


def _skills(client):
    return client.get("/v1/skills").json()["skills"]


def _refused(body, said):
    return body["ok"] is False and said in body["error"].lower()


def _until_done(ws):
    seen = []
    while True:
        event = ws.receive_json()
        seen.append(event)
        if event["type"] in {"turn_done", "input_rejected"}:
            return seen


# -- the settings page ---------------------------------------------------------------------------


def test_a_made_skill_is_listed_with_everything_the_page_shows(tmp_path):
    client, _, _ = _client(tmp_path)
    assert client.post("/v1/skills", json=GREET).json()["ok"] is True
    [row] = _skills(client)
    assert row == {
        "name": "greet", "description": "says hello", "instructions": "Say hello warmly.", "scope": "global", "compat": False,
        "source": "local", "enabled": True, "path": row["path"], "files": 0, "when_to_use": "", "summary": "", "license": "",
        "compatibility": "", "metadata": {}, "model_invocable": True, "user_invocable": True, "uses": 0, "last_used_at": None,
    }


@pytest.mark.parametrize("body", [{}, {"name": "", "instructions": "x"}, {"name": "ok-name", "instructions": "   "}])
def test_a_skill_without_a_name_or_instructions_is_refused(tmp_path, body):
    client, _, _ = _client(tmp_path)
    refused = client.post("/v1/skills", json=body).json()
    assert refused["ok"] is False and refused["error"]


def test_a_taken_name_is_refused(tmp_path):
    client, _, _ = _client(tmp_path)
    client.post("/v1/skills", json=GREET)
    assert _refused(client.post("/v1/skills", json=GREET).json(), "already exists")


def test_a_skill_is_edited_and_switched_off_and_an_unknown_one_is_refused(tmp_path):
    client, _, _ = _client(tmp_path)
    client.post("/v1/skills", json=GREET)
    assert client.patch("/v1/skills/greet", json={"description": "hi", "enabled": False}).json()["ok"] is True
    row = _skills(client)[0]
    assert row["description"] == "hi" and row["enabled"] is False
    assert client.patch("/v1/skills/ghost", json={"description": "x"}).json()["ok"] is False


def test_a_blank_description_is_refused_when_made_or_edited(tmp_path):
    """P0-3: the description is the model's menu entry."""
    client, _, _ = _client(tmp_path)
    assert _refused(client.post("/v1/skills", json={"name": "nodesc", "description": "  ", "instructions": "x"}).json(), "description")
    assert _skills(client) == []
    client.post("/v1/skills", json=GREET)
    assert _refused(client.patch("/v1/skills/greet", json={"description": " "}).json(), "description")
    assert _skills(client)[0]["description"] == "says hello"


def test_a_deleted_skill_is_gone_and_deleting_again_is_refused(tmp_path):
    client, _, _ = _client(tmp_path)
    client.post("/v1/skills", json=GREET)
    assert client.delete("/v1/skills/greet").json()["ok"] is True and _skills(client) == []
    assert client.delete("/v1/skills/greet").json()["ok"] is False


def test_a_skill_moves_into_a_project_and_a_move_onto_a_taken_name_is_refused(tmp_path):
    client, _, _ = _client(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    client.post("/v1/skills", json=GREET)
    moved = client.post("/v1/skills/greet/move", json={"scope": "project", "workspace": str(project)}).json()
    assert moved["ok"] is True and moved["skill"]["scope"] == "project"
    client.post("/v1/skills", json=GREET)  # a global one again, so moving back collides
    assert _refused(client.post("/v1/skills/greet/move", json={"scope": "global", "workspace": str(project)}).json(), "already exists")


def test_a_project_skill_needs_a_folder_that_exists(tmp_path):
    client, _, _ = _client(tmp_path)
    assert _refused(client.post("/v1/skills", json={**GREET, "scope": "project", "workspace": str(tmp_path / "nope")}).json(), "no project folder")


def test_a_conversations_scratch_folder_is_no_project_for_a_skill_but_a_skill_can_leave_one(tmp_path):
    """Making, moving in and confirming an upload all refuse it; moving out is the way back."""
    client, manager, _ = _client(tmp_path)
    scratch_base = tmp_path / "scratchpads"
    manager.set_scratch_base(str(scratch_base))
    scratch = scratch_base / "6d57038c-50d"
    (scratch / ".reeve" / "skills").mkdir(parents=True)
    into_scratch = {"scope": "project", "workspace": str(scratch)}

    assert _refused(client.post("/v1/skills", json={**GREET, **into_scratch}).json(), "temporary")
    client.post("/v1/skills", json=GREET)
    assert _refused(client.post("/v1/skills/greet/move", json=into_scratch).json(), "temporary")
    preview = client.post("/v1/skills/upload", json={"data_b64": _zip_b64({"zipped/SKILL.md": "---\nname: zipped\ndescription: d\n---\nbody\n"})}).json()
    assert _refused(client.post("/v1/skills/upload/confirm", json={"token": preview["token"], **into_scratch}).json(), "temporary")

    manager.skill_store.create(name="stranded", description="d", instructions="x", scope="project", workspace=scratch)
    rescued = client.post("/v1/skills/stranded/move", json={"scope": "global", "workspace": str(scratch)}).json()
    assert rescued["ok"] is True and rescued["skill"]["scope"] == "global"


def test_a_name_that_climbs_out_is_refused(tmp_path):
    client, _, _ = _client(tmp_path)
    # Encoded: the HTTP client would fold a literal /.. away itself.
    assert client.delete("/v1/skills/%2e%2e").json()["ok"] is False
    assert client.post("/v1/skills", json={**GREET, "name": "..%2Fevil"}).json()["ok"] is False


def test_reveal_unknown_skill_is_a_friendly_error(tmp_path):
    client, _, _ = _client(tmp_path)
    refused = client.post("/v1/skills/nope/reveal", json={}).json()
    assert refused["ok"] is False and "nope" in refused["error"]


# -- uploading -----------------------------------------------------------------------------------


def test_an_upload_is_previewed_installs_nothing_and_is_kept_once_confirmed(tmp_path):
    client, _, _ = _client(tmp_path)
    preview = client.post("/v1/skills/upload", json={"data_b64": _zip_b64({"greet/SKILL.md": "---\nname: greet\ndescription: says hello\n---\nSay hello.\n"})}).json()
    assert preview["ok"] is True and preview["name"] == "greet" and _skills(client) == []
    assert client.post("/v1/skills/upload/confirm", json={"token": preview["token"]}).json()["ok"] is True
    assert _skills(client)[0]["source"] == "uploaded"


def test_a_broken_upload_says_what_is_wrong_in_its_own_terms(tmp_path):
    client, _, _ = _client(tmp_path)

    def upload(data: bytes, filename=None):
        return client.post("/v1/skills/upload", json={"data_b64": base64.b64encode(data).decode(), **({"filename": filename} if filename else {})}).json()

    assert _refused(upload(b"not a zip", "x.zip"), "zip")
    assert _refused(upload(b"no frontmatter", "a.md"), "frontmatter")
    assert client.post("/v1/skills/upload", json={}).json()["ok"] is False
    assert client.post("/v1/skills/upload", json={"data_b64": "!!!"}).json()["ok"] is False


def test_draft_endpoint_is_gone(tmp_path):
    """Drafting a skill in Settings went with the flow where workers wrote skills: a skill is
    made in a conversation that ends in save_skill."""
    client, _, _ = _client(tmp_path)
    # the path now reaches PATCH /v1/skills/{name}, so it may answer 405 rather than 404; neither drafts
    assert client.post("/v1/skills/draft", json={"description": "x"}).status_code in (404, 405)


# -- one conversation's skills -------------------------------------------------------------------


def test_a_skill_muted_in_one_conversation_stays_on_in_another_until_cleared(tmp_path):
    client, _, _ = _client(tmp_path)
    client.post("/v1/skills", json=GREET)

    def view(session, body=None):
        if body is None:
            return client.get(f"/v1/sessions/{session}/skills").json()["skills"]
        return client.post(f"/v1/sessions/{session}/skills", json=body).json()["skills"]

    assert view("s1") == [{"name": "greet", "description": "says hello", "summary": "", "scope": "global", "enabled": True, "model_invocable": True, "user_invocable": True, "uses": 0, "last_used_at": None}]
    assert view("s1", {"skill": "greet", "enabled": False})[0]["enabled"] is False
    assert view("s2")[0]["enabled"] is True
    assert view("s1", {"skill": "greet", "clear": True})[0]["enabled"] is True
    assert client.post("/v1/sessions/s1/skills", json={}).json()["ok"] is False


def test_the_engine_offers_only_the_skills_that_are_on_and_in_the_live_context(tmp_path):
    from harness.compose import build_engine
    from harness.modes.agents.chat import chat_agent

    client, manager, _ = _client(tmp_path)
    client.post("/v1/skills", json=GREET)
    client.post("/v1/skills", json={"name": "hidden", "description": "off", "instructions": "x"})
    client.patch("/v1/skills/hidden", json={"enabled": False})
    # An agent without a workspace: the persona is gone, its builder is not.
    engine = build_engine(agent=chat_agent(), provider=ScriptedProvider(), skill_filter=lambda: manager.effective_skill_names("s1"))
    menu = engine.context_provider()  # the per-turn block, not the system prompt
    assert "greet" in menu and "hidden" not in menu
    assert "greet" not in engine.messages[0]["content"]


# -- running a skill by name ---------------------------------------------------------------------


def test_a_skill_run_by_name_frames_the_turn_for_the_model_and_shows_what_was_typed(tmp_path):
    """The model is told to load the skill; the transcript shows "/greet hello"."""
    client, _, provider = _client(tmp_path, [AssistantTurn(text="done")])
    client.post("/v1/skills", json=GREET)
    with client.websocket_connect("/ws/session/s1?agent=chat") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "hello", "skill": "greet"})
        events = _until_done(ws)
    framed = str(provider.seen[-1][-1]["content"])
    assert 'load_skill("greet")' in framed and "hello" in framed
    start = next(e for e in events if e["type"] == "turn_start")
    assert start["data"]["display"] == "/greet hello" and "load_skill" in str(start["data"]["input"])
    stored = next(m for m in client.get("/v1/sessions/s1/messages").json()["messages"] if m["role"] == "user")
    assert stored["_display"] == "/greet hello" and "load_skill" in str(stored["content"])


def test_an_unknown_or_muted_skill_is_refused_and_the_socket_keeps_working(tmp_path):
    client, _, provider = _client(tmp_path, [AssistantTurn(text="ok")])
    client.post("/v1/skills", json=GREET)
    client.post("/v1/sessions/s1/skills", json={"skill": "greet", "enabled": False})
    with client.websocket_connect("/ws/session/s1?agent=chat") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "x", "skill": "ghost"})
        refused = ws.receive_json()
        assert refused["type"] == "input_rejected" and "not available" in refused["data"]["error"]
        # A muted one is refused the same way, never switched back on quietly.
        for name in ("greet", "  "):
            ws.send_json({"type": "user_message", "text": "x", "skill": name})
            assert ws.receive_json()["type"] == "input_rejected"
        ws.send_json({"type": "user_message", "text": "plain"})
        assert any(e["type"] == "turn_done" for e in _until_done(ws))
    # No user message ever carried the framing (the system prompt may name load_skill).
    users = [str(m.get("content")) for sent in provider.seen for m in sent if m.get("role") == "user"]
    assert all("Use the skill" not in u for u in users) and any("plain" in u for u in users)


# -- P1-7 force-run policy + P1-2 compat rows over HTTP --------------------------------


def _write_skill_at(base, name: str, extra: str = "", body: str = "body") -> None:
    folder = base / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n{extra}\n---\n\n{body}\n", encoding="utf-8")


def test_ws_force_run_refuses_user_invocable_false(tmp_path):
    """`user-invocable: false` skills are model-only — the composer hides them, and the
    server refuses a stale force-run frame with a visible error (never a silent no-op)."""
    client, manager, _ = _client(tmp_path, [AssistantTurn(text="done")])
    _write_skill_at(manager.skill_store.global_dir, "model-only", "user-invocable: false")
    with client.websocket_connect("/ws/session/s1?agent=chat") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "x", "skill": "model-only"})
        refused = ws.receive_json()
        assert refused["type"] == "input_rejected" and "on its own" in refused["data"]["error"]
        ws.send_json({"type": "user_message", "text": "plain"})
        assert any(e["type"] == "turn_done" for e in _until_done(ws))  # the socket stayed healthy


def test_ws_force_run_allows_disable_model_invocation(tmp_path):
    """The other flag permits exactly this: a user-run skill the model never sees."""
    client, manager, provider = _client(tmp_path, [AssistantTurn(text="done")])
    _write_skill_at(manager.skill_store.global_dir, "user-only", "disable-model-invocation: true")
    with client.websocket_connect("/ws/session/s1?agent=chat") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "hello", "skill": "user-only"})
        _until_done(ws)
    assert 'load_skill("user-only")' in str(provider.seen[-1][-1]["content"])


def test_session_view_exposes_invocation_flags(tmp_path):
    client, manager, _ = _client(tmp_path)
    _write_skill_at(manager.skill_store.global_dir, "user-only", "disable-model-invocation: true")
    row = client.get("/v1/sessions/s1/skills").json()["skills"][0]
    assert row["model_invocable"] is False and row["user_invocable"] is True


def test_compat_rows_are_read_only_over_http(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr("harness.extensions.skills.roots.home_dir", lambda: home)
    _write_skill_at(home / ".claude" / "skills", "shared-skill")
    client, _, _ = _client(tmp_path)
    row = next(r for r in _skills(client) if r["name"] == "shared-skill")
    assert row["scope"] == "compat" and row["compat"] is True
    for refused in (
        client.patch("/v1/skills/shared-skill", json={"description": "x"}).json(),
        client.patch("/v1/skills/shared-skill", json={"enabled": False}).json(),
        client.delete("/v1/skills/shared-skill").json(),
    ):
        assert refused["ok"] is False and "shared skills folder" in refused["error"]


# -- community installs over HTTP (batch 3/P2) ----------------------------------------


def _fake_runner(*, command, cwd, timeout):
    from harness.extensions.skills.installer import CliResult

    folder = Path(cwd) / ".agents" / "skills" / "greet"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: greet\ndescription: says hello\n---\nSay hello.\n", encoding="utf-8")
    return CliResult(0)


def test_install_preview_confirm_and_remove_over_http(tmp_path):
    client, manager, _ = _client(tmp_path)
    manager.skill_store._install_runner = _fake_runner
    preview = client.post("/v1/skills/install", json={"source": "owner/repo"}).json()
    assert preview["ok"] is True and preview["skills"][0]["name"] == "greet" and preview["installable"] is True
    assert _skills(client) == []  # preview installs nothing
    confirmed = client.post("/v1/skills/install/confirm", json={"token": preview["token"]}).json()
    assert confirmed["ok"] is True and confirmed["skills"][0]["name"] == "greet"
    body = client.get("/v1/skills").json()
    assert body["skills"][0]["name"] == "greet"
    install = body["installs"]["greet"]
    assert (install["source"], install["sourceType"]) == ("owner/repo", "github") and install["computedHash"].startswith("sha256:")
    assert manager.list_skill_installs()["greet"]["skillPath"] == ".agents/skills/greet/SKILL.md"
    assert client.delete("/v1/skills/greet").json()["ok"] is True
    assert client.get("/v1/skills").json()["installs"] == {}  # remove prunes the lock


def test_install_over_http_reports_errors(tmp_path):
    from harness.extensions.skills.installer import CliResult

    client, manager, _ = _client(tmp_path)
    assert _refused(client.post("/v1/skills/install", json={"source": ""}).json(), "source")
    manager.skill_store._install_runner = lambda *, command, cwd, timeout: CliResult(1, stderr="fatal: repository not found")
    assert _refused(client.post("/v1/skills/install", json={"source": "owner/missing"}).json(), "repository not found")
    assert _refused(client.post("/v1/skills/install/confirm", json={"token": "nope"}).json(), "expired")


def test_install_over_http_refuses_shadowing_and_requires_overwrite(tmp_path):
    client, manager, _ = _client(tmp_path)
    manager.skill_store._install_runner = _fake_runner
    # A compat copy of the same name → blocked preview + refused confirm.
    compat = tmp_path / "home" / ".agents" / "skills" / "greet"
    compat.mkdir(parents=True)
    (compat / "SKILL.md").write_text("---\nname: greet\ndescription: community\n---\nbody", encoding="utf-8")
    manager.skill_store.home_dir = tmp_path / "home"
    preview = client.post("/v1/skills/install", json={"source": "owner/repo"}).json()
    assert preview["installable"] is False and "shared skills folder" in preview["skills"][0]["blocked"]
    refused = client.post("/v1/skills/install/confirm", json={"token": preview["token"]}).json()
    assert refused["ok"] is False and "shared skills folder" in refused["error"]
    # A product copy → preview says replaces; confirm needs {overwrite: true}.
    manager.skill_store.home_dir = None
    client.post("/v1/skills", json=GREET)
    preview = client.post("/v1/skills/install", json={"source": "owner/repo"}).json()
    assert preview["skills"][0]["replaces"] is True
    refused = client.post("/v1/skills/install/confirm", json={"token": preview["token"]}).json()
    assert refused["ok"] is False and "already in your skills" in refused["error"]
    assert client.post("/v1/skills/install/confirm", json={"token": preview["token"], "overwrite": True}).json()["ok"] is True
