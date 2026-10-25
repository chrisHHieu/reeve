"""`shell_task_output` looks at a background job's whole output: a search, and a window from a
line — without moving the cursor the next plain read starts from."""

from __future__ import annotations

import sys
import time

from harness.tools.contract import contract_of, safe_facts
from harness.tools.shell import LocalExecutor, shell_tools
from harness.tools.shell_output_read import lines_from, search_lines
from harness.tools.shell_present import _shell_task_render

LOG = [f"line {i}\n" for i in range(1, 9)] + ["ERROR: disk full\n", "line 10\n", "error again\n"]


def test_a_search_numbers_every_matching_line_of_the_whole_output():
    found = search_lines(LOG, "error", ignore_case=True)
    assert found["output"] == "9: ERROR: disk full\n11: error again"
    assert (found["matches"], found["total_lines"]) == (2, 11)
    assert search_lines(LOG, "error")["matches"] == 1
    assert "invalid pattern" in search_lines(LOG, "(")["error"]


def test_a_window_says_where_the_next_one_starts():
    window = lines_from(LOG, 8, budget=40)
    assert window["output"].splitlines()[0] == "line 8"
    assert window["next_from_line"] == window["to_line"] + 1
    assert "past the end" in lines_from(LOG, 50, budget=1000)["note"]


def test_on_a_real_job_the_plain_read_still_starts_where_it_stopped(tmp_path):
    ex = LocalExecutor(cwd=tmp_path, default_timeout=30)
    try:
        script = tmp_path / "job.py"
        script.write_text(
            "for i in range(1, 6):\n    print('step', i, flush=True)\nprint('FAILED: step 3', flush=True)\n",
            encoding="utf-8",
        )
        # cmd on Windows: a quoted path alone is a string to PowerShell, not a program to run.
        started = ex.run_background(
            f'"{sys.executable}" "{script}"', shell="cmd" if sys.platform == "win32" else None
        )
        task_id = started["task_id"]
        tool = {t.__name__: t for t in shell_tools(ex)}["shell_task_output"]
        for _ in range(100):
            if tool(task_id=task_id, pattern="FAILED")["matches"]:
                break
            time.sleep(0.05)

        found = tool(task_id=task_id, pattern="failed", ignore_case=True)
        assert found["output"].endswith("FAILED: step 3") and found["matches"] == 1
        text = _shell_task_render({"task_id": task_id, "pattern": "failed"}, found)
        assert "1 of" in text and "lines match 'failed'" in text
        facts = safe_facts(contract_of(tool), "shell_task_output", {"pattern": "failed"}, found)
        assert facts["read"] == "search" and facts["matches"] == 1

        window = tool(task_id=task_id, from_line=2)
        assert window["output"].splitlines()[0].startswith("step 2")

        # Neither look moved the cursor: the plain read returns everything, once.
        plain = tool(task_id=task_id, wait=10)
        assert "step 1" in plain["output"] and "FAILED: step 3" in plain["output"]
        assert "step 1" not in tool(task_id=task_id)["output"]
    finally:
        ex.close()
