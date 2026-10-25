"""The user memory: who the person is, one directory for every project, written
by the model with the ordinary file tools without an ask, and loaded into every
conversation. Scripted provider, tmp state dirs, no network."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from harness.context.memory.auto_memory import INDEX_NAME, PROJECT, USER, AutoMemory
from harness.context.memory.migrate_user import MARKER, migrate_sqlite_facts
from harness.context.memory.prompt import memory_section
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.interface.server import SessionManager, create_app


class Scripted(ProviderClient):
    def __init__(self, *turns: AssistantTurn):
        self.turns = list(turns)
        self.system: list[str] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.system.append(next((m["content"] for m in messages if m["role"] == "system"), ""))
        return self.turns.pop(0) if self.turns else AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _write_call(path: Path, content: str) -> AssistantTurn:
    return AssistantTurn(
        tool_calls=[ToolCall(id="w1", name="write_file", arguments={"path": str(path), "content": content})],
        finish_reason="tool_calls",
    )


FACT = "---\nname: role\ndescription: Product manager, not a developer\nmetadata:\n  type: user\n---\n\nThe user is a PM.\n"


def _drain(ws) -> list[dict]:
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] == "turn_done":
            return frames


# -- the directory ---------------------------------------------------------------------------


def test_the_old_user_directory_can_still_be_listed_for_review(tmp_path):
    """No session loads or writes it; Settings lists what is there so the person
    can read it, move a line into their instructions, or delete it."""
    user = AutoMemory(tmp_path / "memory", USER)
    assert user.save_memory(title="Role", description="PM", note_type="user", body="The user is a PM.")["ok"]
    names = [f["name"] for f in user.list_files()]
    assert "role.md" in names and "MEMORY.md" in names


def test_forget_removes_the_file_and_its_index_line(tmp_path):
    user = AutoMemory(tmp_path / "memory", USER)
    name = user.save_memory(title="Role", description="PM", note_type="user", body="The user is a PM.")["name"]
    user.save_memory(title="Lang", description="Vietnamese", note_type="feedback", body="Answer in Vietnamese.")
    assert user.forget(name)["ok"]
    index = (tmp_path / "memory" / INDEX_NAME).read_text(encoding="utf-8")
    assert f"({name})" not in index and "Lang" in index


# -- in a conversation -----------------------------------------------------------------------


# -- the one-time move from SQLite -----------------------------------------------------------


def _sqlite(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "create table memories (id integer primary key, scope text, content text, summary text, "
        "kind text, workspace text, superseded_at text)"
    )
    con.executemany(
        "insert into memories (scope, content, summary, kind, workspace, superseded_at) values (?,?,?,?,?,?)",
        [
            ("global", "Prefers answers in Vietnamese", "Answer in Vietnamese", "preference", None, None),
            ("global", "Works as a product manager", "", "entity", None, None),
            ("global", "Old role", "", "entity", None, "2026-09-01"),
            ("workspace", "The deploy runs on Fridays", "Friday deploys", "note", "proj-key", None),
        ],
    )
    con.commit()
    con.close()


def test_the_sqlite_facts_move_into_files_once_with_a_backup(tmp_path):
    db = tmp_path / "reeve.db"
    _sqlite(db)
    user = AutoMemory(tmp_path / "memory", USER)
    projects: dict[str, AutoMemory] = {}

    def project_for(key: str) -> AutoMemory:
        return projects.setdefault(key, AutoMemory(tmp_path / "projects" / key / "memory"))

    done = migrate_sqlite_facts(db, user, project_memory_for=project_for)

    assert done == {"user": 2, "project": 1, "skipped": 0}
    files = {f["name"]: f for f in user.list_files() if f["name"] != INDEX_NAME}
    assert sorted(f["type"] for f in files.values()) == ["feedback", "user"]
    assert "Old role" not in "".join(user.read(n)["content"] for n in files)
    assert [f["type"] for f in projects["proj-key"].list_files() if f["name"] != INDEX_NAME] == ["project"]
    backup = next((tmp_path / "memory" / ".history").glob("sqlite-memories-*.json"))
    assert len(json.loads(backup.read_text(encoding="utf-8"))) == 4  # every row, superseded too
    assert (tmp_path / "memory" / MARKER).is_file()
    assert migrate_sqlite_facts(db, user, project_memory_for=project_for) == {"skipped": "already migrated"}


# -- tidying: the dream pass, and the old scratch memories -----------------------------------


def _grown(am: AutoMemory, n: int) -> None:
    for i in range(n):
        am.save_memory(title=f"Fact {i}", description=f"hook {i}", note_type="user", body=f"fact {i}")


def test_old_scratch_memories_are_parked_whole_and_nothing_else_moves(tmp_path):
    from harness.context.memory.scratch_archive import ARCHIVE_DIR, archive_scratch_memories

    projects = tmp_path / "projects"
    names = [
        "C-Users-me-Reeve-1e40a077-383",  # str(uuid4())[:12]
        "C-Users-me-Reeve-67ae406347b0",  # uuid4().hex[:12]
        "C-Users-me-Reeve-67ae406347b0-cv",  # a folder inside one
        "C-Users-me-Reeve-notes",  # a sibling folder that is not a conversation
        "C-Users-me-Reeveship",
        "general",
        "D-Project-app",
    ]
    for name in names:
        (projects / name / "memory").mkdir(parents=True)
        (projects / name / "memory" / "MEMORY.md").write_text("- [a](a.md)\n", encoding="utf-8")

    moved = archive_scratch_memories(projects, "C-Users-me-Reeve")

    assert moved == names[:3]
    assert sorted(p.name for p in projects.iterdir()) == sorted([ARCHIVE_DIR, *names[3:]])
    assert (projects / ARCHIVE_DIR / names[0] / "memory" / "MEMORY.md").is_file()
    assert archive_scratch_memories(projects, "C-Users-me-Reeve") == []
