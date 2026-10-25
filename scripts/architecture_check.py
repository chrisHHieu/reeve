"""Executable architecture policy — `architecture-policy.toml`, with a baseline.

Rules a reviewer cannot see (file size, the layer order of the components) run in CI,
violations are fingerprinted so a BASELINE can pin the ones that already exist, and only
NEW violations fail the build. The baseline is the honest half: it says "we know, and it
is not growing" instead of either ignoring the problem or blocking every change until a
2700-line file is split.

The layer rule (`[layers]`): every file belongs to one component of the harness
(`docs/architecture.md`), the components have an order, lowest first, and a file may import
its own component and the ones below it, never one above. A file is in a component by where
it lives (`harness/<component>/`). With a total order there is no cycle between components
to look for: a cycle would need one import going up.

    .venv\\Scripts\\python.exe scripts/architecture_check.py check [--changed]
    ... report [--markdown]
    ... baseline:update

`--changed` limits the SIZE rule to files touched in git (plus untracked) — a long file
nobody touched is not this change's problem — while the layer rule always runs, because an
import added anywhere can break the order.

Fingerprints are `sha256(rule \\0 file \\0 detail)[:16]`: line numbers are deliberately not
in the detail, so moving a violation down a file does not un-baseline it.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO / "architecture-policy.toml"
BASELINE_PATH = REPO / "architecture-baseline.json"


@dataclass(frozen=True)
class Violation:
    rule: str
    file: str
    detail: str
    #: Display-only extra (the numbers). NEVER part of the fingerprint: a baselined file
    #: that grows must keep its identity, or every edit would read as a new violation and
    #: the growth allowance could never apply.
    note: str = ""
    #: The file's size when the rule is `max-file-lines` (drives the growth allowance).
    lines: int = 0

    @property
    def fingerprint(self) -> str:
        raw = f"{self.rule}\0{self.file}\0{self.detail}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    def as_dict(self) -> dict:
        entry = {"rule": self.rule, "file": self.file, "detail": self.detail}
        if self.note:
            entry["note"] = self.note
        if self.lines:
            entry["lines"] = self.lines
        return entry


def policy_for(root: Path) -> Path:
    """The policy beside the tree being checked — a test tree or a second checkout is
    governed by ITS policy, never by this one's."""
    return root / "architecture-policy.toml"


def baseline_for(root: Path) -> Path:
    return root / "architecture-baseline.json"


def load_policy(path: Path = POLICY_PATH) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, dict]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {entry["fingerprint"]: entry for entry in data.get("violations", [])}


def _layers(policy: dict) -> list[str]:
    """The layer order of the components, lowest first."""
    return list((policy.get("layers") or {}).get("order", []))


def _component_of(relative: str, order: list[str]) -> str:
    """The component a file belongs to: `harness/<component>/...` by its folder, and the two
    files at the package root by name."""
    parts = relative.split("/")
    if parts[0] == "harness":
        if len(parts) > 2 and parts[1] in order:
            return parts[1]
        return {"__init__.py": "root", "compose.py": "compose"}.get(parts[1], "") if len(parts) == 2 else ""
    return ""


def _module_index(files: Iterable[str]) -> dict[str, str]:
    """Dotted module name -> the file that defines it, a package by its `__init__`."""
    index: dict[str, str] = {}
    for relative in files:
        dotted = relative[: -len(".py")].replace("/", ".")
        if dotted.endswith(".__init__"):
            dotted = dotted[: -len(".__init__")]
        index[dotted] = relative
    return index


def _imported_modules(relative: str, tree: ast.AST) -> Iterable[str]:
    """Every dotted name a module imports (relative imports resolved), the names a
    `from x import y` brings in included: `y` may itself be a module."""
    package = relative.split("/")[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                head = ".".join([*base, *([node.module] if node.module else [])])
            else:
                head = node.module or ""
            if head:
                yield head
                for alias in node.names:
                    yield f"{head}.{alias.name}"


def _defining_file(name: str, index: dict[str, str]) -> str:
    parts = name.split(".")
    while parts:
        found = index.get(".".join(parts))
        if found:
            return found
        parts.pop()
    return ""


def _layer_violations(
    relative: str, tree: ast.AST, order: list[str], index: dict[str, str]
) -> list[Violation]:
    """A file that imports a component above its own. One violation per file and component
    imported, named without the target file so moving the target does not rename it."""
    mine = _component_of(relative, order)
    if mine not in order:
        return []
    rank = {name: i for i, name in enumerate(order)}
    above: dict[str, set[str]] = {}
    for name in _imported_modules(relative, tree):
        target = _defining_file(name, index)
        other = _component_of(target, order) if target and target != relative else ""
        if other in rank and rank[other] > rank[mine]:
            above.setdefault(other, set()).add(target)
    return [
        Violation("layer-violation", relative, f"{mine} must not import {other}", note=", ".join(sorted(hits)[:2]))
        for other, hits in sorted(above.items())
    ]


def _changed_files(root: Path) -> set[str] | None:
    """Repo-relative paths touched in git (working tree + untracked), or None when git
    cannot answer — in which case the caller checks everything rather than nothing."""
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", "-z", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if out.returncode != 0 or untracked.returncode != 0:
            return None
        names = {n for n in out.stdout.split("\0") if n}
        names |= {n for n in untracked.stdout.split("\0") if n}
        return names
    except OSError:
        return None


def scan(root: Path = REPO, policy: dict | None = None) -> list[Violation]:
    policy = policy or load_policy(policy_for(root))
    rules = policy.get("rules", {})
    paths = policy.get("paths", {})
    exemptions = set(policy.get("exemptions", {}).get("max_file_lines", []))

    include = paths.get("include", ["harness"])
    exclude = tuple(paths.get("exclude", []))
    max_lines = int(rules.get("max_file_lines", 0))
    order = _layers(policy)
    violations: list[Violation] = []

    files: list[Path] = []
    for entry in include:
        base = root / entry
        if base.is_dir():
            files.extend(sorted(p for p in base.rglob("*.py") if "__pycache__" not in str(p)))
    kept = [p for p in files if not any(part in p.relative_to(root).as_posix() for part in exclude)]
    index = _module_index(p.relative_to(root).as_posix() for p in kept) if order else {}
    for path in kept:
        relative = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            violations.append(Violation("unreadable", relative, str(exc)))
            continue
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError as exc:
            violations.append(Violation("unparsable", relative, f"line {exc.lineno}"))
            continue

        lines = source.count("\n") + (0 if source.endswith("\n") else 1)
        is_test = Path(relative).name.startswith("test_") or "/tests/" in relative
        if max_lines and not is_test and relative not in exemptions and lines > max_lines:
            violations.append(
                Violation(
                    "max-file-lines",
                    relative,
                    f"over the {max_lines}-line ceiling",
                    note=f"{lines} lines",
                    lines=lines,
                )
            )

        if order:
            violations.extend(_layer_violations(relative, tree, order, index))

    return sorted(violations, key=lambda v: (v.rule, v.file, v.detail))


def classify(
    violations: Iterable[Violation], baseline: dict[str, dict], growth: float
) -> tuple[list[Violation], list[Violation]]:
    """(new, baselined). A baselined size violation is NEW again once the file grew past
    the recorded size by more than `growth`."""
    new: list[Violation] = []
    old: list[Violation] = []
    for violation in violations:
        entry = baseline.get(violation.fingerprint)
        if entry is None:
            new.append(violation)
            continue
        recorded = int(entry.get("lines") or 0)
        if recorded and violation.lines > recorded * (1 + growth):
            new.append(
                Violation(
                    violation.rule,
                    violation.file,
                    violation.detail,
                    note=(
                        f"{violation.lines} lines (baselined at {recorded}; "
                        f"growth limit {int(growth * 100)}%)"
                    ),
                    lines=violation.lines,
                )
            )
            continue
        old.append(violation)
    return new, old


def cmd_check(root: Path, *, changed_only: bool) -> int:
    policy = load_policy(policy_for(root))
    baseline = load_baseline(baseline_for(root))
    growth = float(policy.get("limits", {}).get("grandfathered_growth", 0.10))
    violations = scan(root, policy)

    if changed_only:
        changed = _changed_files(root)
        if changed is not None:
            # The size rule follows the change; direction and cycle rules are global —
            # an import added anywhere can break the graph, and they are cheap.
            violations = [
                v
                for v in violations
                if v.rule != "max-file-lines" or v.file in changed
            ]

    new, old = classify(violations, baseline, growth)
    if new:
        for violation in new:
            extra = f" ({violation.note})" if violation.note else ""
            print(f"NEW {violation.rule}: {violation.file} — {violation.detail}{extra}")
        print(
            f"\n{len(new)} new violation(s). Fix them, or — deliberately, in review — "
            "re-baseline with `architecture_check.py baseline:update`."
        )
        return 1
    print(
        f"architecture OK — {len(old)} baselined violation(s), none new"
        + (" (changed files only)" if changed_only else "")
    )
    return 0


def cmd_report(root: Path, *, markdown: bool) -> int:
    policy = load_policy(policy_for(root))
    baseline = load_baseline(baseline_for(root))
    growth = float(policy.get("limits", {}).get("grandfathered_growth", 0.10))
    violations = scan(root, policy)
    new, old = classify(violations, baseline, growth)
    if markdown:
        print("| rule | file | detail | status |")
        print("|---|---|---|---|")
        for violation in sorted(violations, key=lambda v: (v.rule, v.file)):
            status = "NEW" if violation in new else "baselined"
            detail = violation.detail + (f" ({violation.note})" if violation.note else "")
            print(f"| {violation.rule} | {violation.file} | {detail} | {status} |")
        print(f"\n{len(new)} new, {len(old)} baselined.")
    else:
        for violation in violations:
            marker = "NEW " if violation in new else "    "
            extra = f" ({violation.note})" if violation.note else ""
            print(f"{marker}{violation.rule}: {violation.file} — {violation.detail}{extra}")
        print(f"{len(new)} new, {len(old)} baselined.")
    return 0


def cmd_baseline_update(root: Path) -> int:
    policy = load_policy(policy_for(root))
    violations = scan(root, policy)
    entries = []
    for violation in sorted(violations, key=lambda v: v.fingerprint):
        entry = {"fingerprint": violation.fingerprint, **violation.as_dict()}
        entries.append(entry)
    target = baseline_for(root)
    target.write_text(
        json.dumps({"version": 1, "violations": entries}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"baseline: {len(entries)} violation(s) recorded in {target.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="architecture_check")
    parser.add_argument("command", choices=["check", "report", "baseline:update"])
    parser.add_argument("--root", default=str(REPO), help="repository root to check")
    parser.add_argument("--changed", action="store_true", help="size rule: changed files only")
    parser.add_argument("--markdown", action="store_true", help="report as a markdown table")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.command == "check":
        return cmd_check(root, changed_only=args.changed)
    if args.command == "report":
        return cmd_report(root, markdown=args.markdown)
    return cmd_baseline_update(root)


if __name__ == "__main__":
    raise SystemExit(main())
