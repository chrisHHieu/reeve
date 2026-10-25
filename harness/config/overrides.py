"""Risk classes the person set themselves, tool by tool, looser or stricter than the default.

Mostly they loosen MCP's careful default, under which every MCP tool counts as `external`: someone
who trusts a server marks its read-only tools `read` and they stop asking. A rule is a glob over
tool names (`mcp__notion__create_page`, `mcp__notion__*`), and of the rules that match a name the
most specific one decides.

**Only the person writes this file; nothing an agent or a package brings ever does.** An agent may
say which tools it wants, but how far they are trusted is the person's call, so loading an agent
never touches this store (`PERMISSIONS-AND-INBOX.md`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Callable, Optional

from harness.safety.risk import RiskClass

_WILDCARDS = frozenset("*?[]")
_EXACT_NAME = 1000  # a rule with no wildcard beats any glob


@dataclass
class _Rule:
    pattern: str
    risk: RiskClass

    @property
    def specificity(self) -> int:
        """How narrowly the rule aims: each literal character counts one, and a pattern with no
        wildcard at all counts more than any glob."""
        literal = sum(ch not in _WILDCARDS for ch in self.pattern)
        return literal if _WILDCARDS & set(self.pattern) else literal + _EXACT_NAME

    def matches(self, tool_name: str) -> bool:
        return fnmatchcase(tool_name, self.pattern)


def _parse(entry: dict) -> Optional[_Rule]:
    try:
        return _Rule(str(entry["pattern"]), RiskClass(str(entry["risk"])))
    except (KeyError, ValueError, TypeError):
        return None  # a broken rule is skipped; the others still hold


class RiskOverrideStore:
    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._rules: list[_Rule] = self._load()

    def _load(self) -> list[_Rule]:
        if self.path is None or not self.path.is_file():
            return []
        stored = json.loads(self.path.read_text(encoding="utf-8")).get("rules", [])
        return [rule for rule in map(_parse, stored) if rule is not None]

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rules = [{"pattern": rule.pattern, "risk": rule.risk.value} for rule in self._rules]
        self.path.write_text(json.dumps({"rules": rules}, indent=2), encoding="utf-8")

    def set_rule(self, pattern: str, risk: RiskClass | str) -> None:
        """Set the class for `pattern`, replacing a rule with the same pattern. The approval card
        is what usually calls this."""
        rule = _Rule(pattern, RiskClass(risk))
        self._rules = [kept for kept in self._rules if kept.pattern != pattern] + [rule]
        self.save()

    def resolve(self, tool_name: str) -> Optional[RiskClass]:
        """The class the most specific matching rule gives `tool_name` (the earlier of two equally
        specific ones); None when no rule matches."""
        best: Optional[_Rule] = None
        for rule in self._rules:
            if rule.matches(tool_name) and (best is None or rule.specificity > best.specificity):
                best = rule
        return best.risk if best else None

    def resolver(self) -> Callable[[str], Optional[RiskClass]]:
        """What `PermissionEngine.risk_overrides` and `risk.classify` are given."""
        return self.resolve
