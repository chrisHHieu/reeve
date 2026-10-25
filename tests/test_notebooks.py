"""A notebook is read cell by cell and edited cell by cell.

Read raw, a notebook is JSON whose source lines are quoted, escaped strings and whose pictures are
base64; edited with `replace_in_file`, one wrong escape leaves a file Jupyter cannot open. These
pin what `read_file` shows of one and what `edit_notebook` writes back.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from harness.safety.permissions import Mode, PermissionEngine
from harness.safety.risk import RiskClass, classify
from harness.tools.files import file_tools
from harness.tools.notebook_tool import notebook_tools
from harness.tools.task_agents import DENY, evaluate

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 32).decode()


def _notebook(path: Path, *, ids: bool = True, newline: str = "\n") -> Path:
    cells = [
        {"cell_type": "markdown", "metadata": {}, "source": ["# Sales\n", "by month"]},
        {
            "cell_type": "code", "metadata": {}, "execution_count": 3,
            "source": ["import pandas as pd\n", "df = pd.read_csv('s.csv')\n", "df.head()"],
            "outputs": [
                {"output_type": "stream", "name": "stdout", "text": ["loaded 12 rows\n"]},
                {"output_type": "execute_result", "execution_count": 3, "metadata": {},
                 "data": {"text/plain": ["   month  total\n", "0  jan    10"]}},
                {"output_type": "display_data", "metadata": {}, "data": {"image/png": _PNG, "text/plain": ["<Figure>"]}},
            ],
        },
        {
            "cell_type": "code", "metadata": {}, "execution_count": 4, "source": "1/0",
            "outputs": [{"output_type": "error", "ename": "ZeroDivisionError", "evalue": "division by zero",
                         "traceback": ["\x1b[0;31m---------\x1b[0m", "Traceback (most recent call last)", "ZeroDivisionError: division by zero"]}],
        },
    ]
    if ids:
        for cell, cid in zip(cells, ("aa11", "bb22", "cc33")):
            cell["id"] = cid
    payload = {"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5 if ids else 4}
    text = json.dumps(payload, indent=1) + "\n"
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return path


def _read(tmp_path, name="sales.ipynb"):
    return file_tools(str(tmp_path))[0](path=name)["content"]


def _edit(tmp_path):
    return notebook_tools(str(tmp_path))[0]


def test_read_file_shows_cells_with_number_id_and_outputs(tmp_path):
    _notebook(tmp_path / "sales.ipynb")
    text = _read(tmp_path)
    assert "--- cell 1 · markdown · id aa11 ---" in text
    assert "--- cell 2 · code · id bb22 · ran 3 ---" in text
    assert "df = pd.read_csv('s.csv')" in text and '\\n"' not in text  # code, not JSON strings
    assert "loaded 12 rows" in text and "0  jan    10" in text
    assert "[image/png output: not shown here]" in text and _PNG[:20] not in text
    assert "ZeroDivisionError: division by zero" in text and "\x1b[" not in text


def test_replace_by_id_rewrites_one_cell_as_valid_nbformat_and_clears_its_outputs(tmp_path):
    path = _notebook(tmp_path / "sales.ipynb", newline="\r\n")
    result = _edit(tmp_path)(path="sales.ipynb", cell="bb22", source="df = load()\ndf.tail()")
    assert result["cell"] == 2 and result["id"] == "bb22" and result["cleared"] is True

    raw = path.read_bytes().decode("utf-8")
    assert "\r\n" in raw and "\n" not in raw.replace("\r\n", "")  # the file's line ending kept
    payload = json.loads(raw)
    edited = payload["cells"][1]
    assert edited["source"] == ["df = load()\n", "df.tail()"]
    assert edited["outputs"] == [] and edited["execution_count"] is None
    assert payload["cells"][0]["source"] == ["# Sales\n", "by month"]  # untouched
    assert payload["cells"][2]["outputs"][0]["ename"] == "ZeroDivisionError"


def test_insert_and_delete_keep_every_other_cell_where_its_id_says(tmp_path):
    path = _notebook(tmp_path / "sales.ipynb")
    edit = _edit(tmp_path)
    added = edit(path="sales.ipynb", cell="aa11", action="insert", cell_type="code", source="x = 1")
    assert added["cell"] == 2 and added["id"] and added["id"] not in ("aa11", "bb22", "cc33")
    first = edit(path="sales.ipynb", cell="0", action="insert", cell_type="markdown", source="intro")
    assert first["cell"] == 1
    # bb22 moved down two places; naming it by id still reaches it.
    assert edit(path="sales.ipynb", cell="bb22", action="delete")["cell"] == 4
    ids = [c.get("id") for c in json.loads(path.read_text(encoding="utf-8"))["cells"]]
    assert ids[1:] == ["aa11", added["id"], "cc33"]


def test_a_notebook_without_ids_is_named_by_number_and_gets_none(tmp_path):
    path = _notebook(tmp_path / "old.ipynb", ids=False)
    edit = _edit(tmp_path)
    assert edit(path="old.ipynb", cell="3", source="2/1")["cell"] == 3
    edit(path="old.ipynb", cell="1", action="insert", cell_type="code", source="y")
    cells = json.loads(path.read_text(encoding="utf-8"))["cells"]
    assert all("id" not in c for c in cells)
    assert "--- cell 2 · code ---" in _read(tmp_path, "old.ipynb")


def test_the_refusals_say_what_to_do(tmp_path):
    _notebook(tmp_path / "sales.ipynb")
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    edit = _edit(tmp_path)
    assert "its cell ids: aa11, bb22, cc33" in edit(path="sales.ipynb", cell="zz99", source="")["error"]
    assert "replace_in_file" in edit(path="a.py", cell="1", source="")["error"]
    assert "needs cell_type" in edit(path="sales.ipynb", cell="aa11", action="insert", source="")["error"]
    with pytest.raises(PermissionError):
        edit(path="../elsewhere.ipynb", cell="1", source="")


def test_it_is_a_write_to_the_permission_engine_and_to_a_read_only_subagent(tmp_path):
    assert classify("edit_notebook", None) is RiskClass.WRITE_LOCAL
    engine = PermissionEngine(workspace_root=tmp_path, mode=Mode.WORKSPACE_WRITE)
    decision = engine.evaluate("edit_notebook", {"path": "sales.ipynb", "cell": "1"})
    assert decision.needs_user and not decision.allowed
    plan = PermissionEngine(workspace_root=tmp_path, mode=Mode.WORKSPACE_WRITE, plan=True)
    assert not plan.evaluate("edit_notebook", {"path": "sales.ipynb", "cell": "1"}).allowed
    assert evaluate("edit_notebook", "sales.ipynb", [("edit", "*", DENY)]) == DENY


def test_read_file_shows_a_picture_by_its_bytes_as_well_as_its_name(tmp_path):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    (tmp_path / "chart.png").write_bytes(png)
    (tmp_path / "shot").write_bytes(png)  # no extension: told apart by its first bytes
    read_file = file_tools(str(tmp_path))[0]
    for name in ("chart.png", "shot"):
        result = read_file(path=name)
        assert result["_attach"][0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert "attached below" in result["shown"]
