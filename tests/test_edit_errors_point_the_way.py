"""A failed edit says where to look (2026-09-25 audit, W9).

"found 0" named no place and "found 2" named no lines, so the model re-read blind or
retried the same text — the costliest loop in this harness. The refusal now
names the lines of every match, or the closest text and its first differing line.
"""

from __future__ import annotations

import pytest

from harness.tools.edit_matchers import closest_region
from harness.tools.file_writers import file_writers

SOURCE = (
    "def div(a, b):\n"
    "    if b == 0:\n"
    "        raise ZeroDivisionError\n"
    "    return a // b\n"
    "\n"
    "def mul(a, b):\n"
    "    return a * b\n"
    "\n"
    "def twice(a):\n"
    "    return a * b\n"
)


@pytest.fixture
def replace(tmp_path):
    (tmp_path / "calc.py").write_text(SOURCE, encoding="utf-8")
    tools = {fn.__name__: fn for fn in file_writers(str(tmp_path))}
    return tools["replace_in_file"]


def test_several_matches_name_their_lines_and_the_ways_out(replace):
    with pytest.raises(ValueError) as caught:
        replace(path="calc.py", old="    return a * b", new="    return b * a")
    message = str(caught.value)
    assert "found 2" in message and "at lines 7, 10" in message
    assert "expected_replacements to 2" in message


def test_no_match_names_the_closest_text_and_the_line_that_differs(replace):
    with pytest.raises(ValueError) as caught:
        replace(
            path="calc.py",
            old="    if b == 0:\n        raise ZeroDivisionError\n    return a / b",
            new="x",
        )
    message = str(caught.value)
    assert "lines 2-4" in message
    assert "first difference at line 4" in message
    assert "'    return a / b'" in message and "'    return a // b'" in message


def test_nothing_alike_says_so_instead_of_inventing_a_place():
    assert closest_region(SOURCE, "completely unrelated text\nwith two lines") is None


def test_a_patch_that_misses_names_its_hunk_and_the_closest_text(tmp_path):
    from harness.modes.agents.base import AgentContext
    from harness.modes.catalog import expand

    (tmp_path / "calc.py").write_text(SOURCE, encoding="utf-8")
    tools = {getattr(t, "__name__", ""): t for t in expand(["files"], AgentContext(workspace=tmp_path))}
    patch = "\n".join([
        "*** Begin Patch",
        "*** Update File: calc.py",
        "@@",
        " def mul(a, b):",
        "-    return a * b",
        "+    return b * a",
        "@@",
        " def div(a, b):",
        "     if b == 0:",
        "-        raise ValueError",
        "+        raise ArithmeticError",
        "*** End Patch",
    ])
    with pytest.raises(ValueError) as caught:
        tools["apply_patch"](patch=patch)
    message = str(caught.value)
    assert "hunk 2 of 2 does not match" in message
    assert "first difference at line 3" in message and "ZeroDivisionError" in message
