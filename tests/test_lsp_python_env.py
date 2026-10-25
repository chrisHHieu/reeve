"""pyright is told the project's own interpreter (harness/integrations/lsp/python_env.py).

Without one it analysed against whatever `python` was on PATH, so every third-party import
read `could not be resolved` — session b3a8491e-9db (2026-09-28) got the same `sqlalchemy`
error after each of eleven edits. The venv is found at the root or, when there is exactly
one, one folder down (`backend/.venv`); the answer reaches pyright through
`workspace/configuration`.
"""

from __future__ import annotations

import os
from pathlib import Path

from harness.integrations.lsp.client import LSPClient
from harness.integrations.lsp.python_env import project_interpreter, python_settings


def _venv(folder: Path) -> Path:
    folder.mkdir(parents=True)
    (folder / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    python = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    return python


def test_the_root_venv_wins(tmp_path):
    root_python = _venv(tmp_path / ".venv")
    _venv(tmp_path / "backend" / ".venv")
    assert project_interpreter(tmp_path) == root_python


def test_one_venv_one_folder_down_is_the_projects(tmp_path):
    # wealthlog's shape: the repo root has no venv, backend/ does.
    python = _venv(tmp_path / "backend" / ".venv")
    (tmp_path / "frontend").mkdir()
    assert project_interpreter(tmp_path) == python
    assert python_settings(tmp_path) == {"python": {"pythonPath": str(python)}}


def test_several_candidates_or_none_is_no_guess(tmp_path):
    _venv(tmp_path / "api" / ".venv")
    _venv(tmp_path / "worker" / "venv")
    assert project_interpreter(tmp_path) is None
    assert python_settings(tmp_path) == {}


def test_a_folder_named_venv_without_pyvenv_cfg_is_not_a_venv(tmp_path):
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    assert project_interpreter(tmp_path) is None


def test_the_handshake_asks_the_server_to_pull_its_settings(tmp_path):
    # The first live check of this change still read "sqlalchemy could not be resolved":
    # the answer below was right, but pyright never asked, because the client did not
    # declare `workspace.configuration`. Measured after the fix on wealthlog's reports.py:
    # 1 unresolved import without the interpreter, 0 with it.
    def params_of(client: LSPClient) -> dict:
        sent: dict = {}

        def request(method, params, **_):
            sent.update(params)
            return {"capabilities": {}}

        client.request = request
        client.notify = lambda *a, **k: None
        client.initialize()
        return sent["capabilities"]["workspace"]

    with_settings = LSPClient(["unused"], tmp_path, settings={"python": {"pythonPath": "/p"}})
    assert params_of(with_settings).get("configuration") is True
    # A server with nothing to be told keeps the handshake it had.
    assert "configuration" not in params_of(LSPClient(["unused"], tmp_path))


def test_the_client_answers_each_requested_section(tmp_path):
    client = LSPClient(["unused"], tmp_path, settings={"python": {"pythonPath": "/p/python"}})
    sent: list[dict] = []
    client._send = sent.append  # the reply, captured instead of written to a pipe
    client._answer(
        7,
        "workspace/configuration",
        {"items": [{"section": "python"}, {"section": "python.analysis"}, {"section": "pyright"}]},
    )
    assert sent[0]["result"] == [{"pythonPath": "/p/python"}, {}, {}]
