"""The architecture policy: a file ceiling with a growing baseline, and the layer order.

Two properties matter, and both are tested on synthetic trees so the rules are pinned
independently of this repo's current state:
- a baselined size violation is GRANDFATHERED at the size it had — a file that keeps
  growing past the allowance is a new violation, which is the difference between "not
  split yet" and "allowed to grow";
- the layer rule is global and never baselined away silently.

The last test runs the checker against THIS repo, so a committed baseline that no longer
matches reality (a file was split, or grew) fails here rather than in CI.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.architecture_check import (
    classify,
    load_baseline,
    main,
    scan,
)

REPO = Path(__file__).resolve().parents[1]

POLICY = """
[rules]
max_file_lines = 40
max_test_file_lines = 0

[limits]
grandfathered_growth = 0.10

[paths]
include = ["harness"]
exclude = ["__pycache__"]

[exemptions]
max_file_lines = []
"""


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (tmp_path / "architecture-policy.toml").write_text(POLICY, encoding="utf-8")
    return tmp_path


def _lines(count: int) -> str:
    return "\n".join(f"x{i} = {i}" for i in range(count)) + "\n"


def test_a_long_file_is_a_violation_and_a_baselined_one_passes(tmp_path):
    root = _tree(tmp_path, {"harness/big.py": _lines(60)})
    violations = scan(root)
    assert [v.rule for v in violations] == ["max-file-lines"]
    assert violations[0].lines == 60

    baseline = {violations[0].fingerprint: violations[0].as_dict()}
    new, old = classify(violations, baseline, 0.10)
    assert new == [] and len(old) == 1


def test_a_baselined_file_that_keeps_growing_is_new_again(tmp_path):
    root = _tree(tmp_path, {"harness/big.py": _lines(60)})
    baseline = {v.fingerprint: v.as_dict() for v in scan(root)}

    (root / "harness" / "big.py").write_text(_lines(64), encoding="utf-8")
    new, old = classify(scan(root), baseline, 0.10)
    assert new == [] and len(old) == 1, "within the 10% allowance"

    (root / "harness" / "big.py").write_text(_lines(80), encoding="utf-8")
    new, _old = classify(scan(root), baseline, 0.10)
    assert [v.rule for v in new] == ["max-file-lines"]
    assert "baselined at 60" in new[0].note


LAYERED = POLICY + """
[layers]
order = ["root", "config", "tools", "loop", "interface", "compose"]
"""


def _layered(tmp_path: Path, files: dict[str, str]) -> Path:
    root = _tree(tmp_path, files)
    (root / "architecture-policy.toml").write_text(LAYERED, encoding="utf-8")
    return root


def _above(root: Path) -> list[tuple[str, str]]:
    return [(v.file, v.detail) for v in scan(root) if v.rule == "layer-violation"]


def test_a_file_importing_a_component_above_its_own_is_a_layer_violation(tmp_path):
    root = _layered(tmp_path, {
        "harness/config/paths.py": "from harness.loop.turn import run\n",
        "harness/tools/files.py": "from ..config import paths\nfrom harness.loop import turn\n",
        "harness/loop/turn.py": "from harness.tools.files import read\nfrom harness.config import paths\n",
    })
    assert _above(root) == [
        ("harness/config/paths.py", "config must not import loop"),
        ("harness/tools/files.py", "tools must not import loop"),
    ]


def test_a_file_may_import_its_own_component_and_those_below(tmp_path):
    root = _layered(tmp_path, {
        "harness/tools/a.py": "from .b import b\nfrom harness.config import paths\n",
        "harness/tools/b.py": "from harness.tools import a\n",
        "harness/config/paths.py": "x = 1\n",
        "harness/__init__.py": "__version__ = '1'\n",
        "harness/loop/turn.py": "from harness import __version__\nfrom harness.tools.a import a\n",
    })
    assert _above(root) == []


def test_the_package_root_files_have_their_own_places(tmp_path):
    root = _layered(tmp_path, {
        "harness/__init__.py": "from harness.compose import build\n",
        "harness/compose.py": "from harness.loop.turn import run\nfrom harness.interface import cli\n",
        "harness/loop/turn.py": "x = 1\n",
        "harness/interface/cli.py": "x = 1\n",
    })
    assert _above(root) == [("harness/__init__.py", "root must not import compose")]


def test_a_layer_violation_is_named_without_its_target_so_a_move_keeps_its_baseline(tmp_path):
    first = _layered(tmp_path / "a", {"harness/config/p.py": "from harness.loop import turn\n",
                                      "harness/loop/turn.py": "x = 1\n"})
    second = _layered(tmp_path / "b", {"harness/config/p.py": "from harness.loop.engine import turn\n",
                                       "harness/loop/engine.py": "turn = 1\n"})
    one = [v for v in scan(first) if v.rule == "layer-violation"]
    two = [v for v in scan(second) if v.rule == "layer-violation"]
    assert one[0].fingerprint == two[0].fingerprint
    assert "harness/loop/turn.py" in one[0].note  # the target is shown, never part of the identity


def test_without_a_layers_section_the_rule_does_not_run(tmp_path):
    root = _tree(tmp_path, {"harness/config/p.py": "from harness.loop import turn\n", "harness/loop/turn.py": "x = 1\n"})
    assert [v for v in scan(root) if v.rule == "layer-violation"] == []


def test_an_unparsable_file_is_a_violation_not_a_crash(tmp_path):
    root = _tree(tmp_path, {"harness/broken.py": "def oops(:\n"})
    assert [v.rule for v in scan(root)] == ["unparsable"]


def test_changed_only_skips_untouched_size_violations_but_not_layers(tmp_path):
    root = _tree(
        tmp_path,
        {
            "harness/old_big.py": _lines(60),
            "harness/config/p.py": "from harness.loop import turn\n",
            "harness/loop/turn.py": "x = 1\n",
        },
    )
    (root / "architecture-policy.toml").write_text(LAYERED, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    subprocess.run(["git", "add", "."], cwd=str(root), check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "seed"],
        cwd=str(root),
        check=True,
    )
    (root / "architecture-baseline.json").write_text(
        json.dumps({"version": 1, "violations": []}), encoding="utf-8"
    )

    assert main(["check", "--root", str(root)]) == 1
    # Nothing changed in git, so the long file is out of scope — the layer violation
    # is not (an import added anywhere can break the order).
    code = main(["check", "--root", str(root), "--changed"])
    assert code == 1


def test_report_markdown_lists_rules_and_status(tmp_path):
    root = _tree(tmp_path, {"harness/big.py": _lines(60)})
    assert main(["report", "--root", str(root), "--markdown"]) == 0


def test_this_repo_passes_with_its_committed_baseline():
    """A baseline that no longer matches reality is worse than none: it hides the fix
    (a file was split and the entry lingers) or the regression (a file grew)."""
    assert main(["check"]) == 0
    entries = load_baseline()
    assert entries, "the baseline records the grandfathers; an empty file means it was lost"
    assert all("fingerprint" in entry for entry in entries.values())


def test_the_baseline_is_current_for_every_recorded_file():
    """Every baselined entry still describes a real violation, and none has grown past the
    allowance — the same check CI runs, stated as a test so a drive-by split is noticed."""
    from scripts.architecture_check import load_policy

    policy = load_policy()
    growth = float(policy["limits"]["grandfathered_growth"])
    baseline = load_baseline()
    current = {v.fingerprint: v for v in scan(REPO)}
    new, _old = classify(current.values(), baseline, growth)
    assert new == [], [f"{v.file}: {v.detail}" for v in new]
    stale = set(baseline) - set(current)
    assert stale == set(), f"baselined violations that no longer exist: {len(stale)}"
