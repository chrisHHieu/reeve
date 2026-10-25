"""A later change to the app that moves a reviewed design (step 5).

Pinned: "Looks good" records the app's tokens the design's colours resolve to; a write of a theme file
that changes or removes one is said in that write's result and logged as `design/drift`; a write of
anything else, a token no reviewed design uses, or no review at all says nothing.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from harness.interface.server.manager.design_review import DesignReviewMixin
from harness.llm.providers import AssistantTurn, ToolCall
from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry
from harness.tools.design.check import DesignReport
from harness.tools.design.drift import design_colors, drift_text, find_drift, reviewed_tokens
from harness.tools.design.app_read import read_app
from harness.tools.design.handoff import Locked, write_lock
from harness.tools.design.locks import read_lock
from test_design_check import _Scripted, _approve
from test_design_lock import _Manager

_CONFIG = """export default { theme: { extend: { colors: { brand: "#1f5c4a", paper: "#fbf7f0", other: "#123456" } } } };\n"""


def _project(root: Path) -> Path:
    (root / "package.json").write_text('{"dependencies": {"react": "18"}, "devDependencies": {"tailwindcss": "3"}}', encoding="utf-8")
    (root / "tailwind.config.js").write_text(_CONFIG, encoding="utf-8")
    page = root / "design" / "pricing.html"
    page.parent.mkdir(parents=True)
    page.write_text("<!doctype html><title>x</title>", encoding="utf-8")
    system = {"width": 1440, "colors": {"background": [{"key": "#fbf7f0", "where": "body"}, {"key": "#1f5c4a", "where": "button"}]}}
    write_lock(Locked(page=page, shown="design/pricing.html", sha256="ab" * 32,
                      report=DesignReport(shown="design/pricing.html", widths=[1440]), system=[system]))
    return page


class _ReviewManager(_Manager, DesignReviewMixin):
    pass


def _review(root: Path) -> None:
    done = _ReviewManager(root).design_review_done("s1", "design/pricing.html", "http://localhost:5173/pricing", 0)
    assert done["ok"], done


def test_a_review_records_the_tokens_the_design_uses(tmp_path):
    page = _project(tmp_path)
    _review(tmp_path)
    reviewed = read_lock(page)["reviewed"]
    assert reviewed["tokens"] == {
        "paper": {"value": "#fbf7f0", "source": "tailwind.config.js"},
        "brand": {"value": "#1f5c4a", "source": "tailwind.config.js"},
    }
    assert reviewed_tokens(read_app(tmp_path), design_colors(read_lock(page))) == reviewed["tokens"]


def test_a_theme_write_that_moves_a_reviewed_token_is_said(tmp_path):
    _project(tmp_path)
    config = tmp_path / "tailwind.config.js"
    assert find_drift(tmp_path, [config]) == []  # nothing reviewed yet
    _review(tmp_path)
    assert find_drift(tmp_path, [config]) == []  # nothing moved
    config.write_text(_CONFIG.replace("#123456", "#654321"), encoding="utf-8")
    assert find_drift(tmp_path, [config]) == []  # a token the design does not use
    config.write_text(_CONFIG.replace("#1f5c4a", "#2563eb").replace('paper: "#fbf7f0", ', ""), encoding="utf-8")
    drifts = find_drift(tmp_path, [config])
    assert [(d.page, d.token, d.before, d.after) for d in drifts] == [
        ("design/pricing.html", "paper", "#fbf7f0", None),
        ("design/pricing.html", "brand", "#1f5c4a", "#2563eb"),
    ]
    text = drift_text(drifts)
    assert text.startswith("[design drift] This write changed tokens that a design the person reviewed in the app uses:")
    assert "- design/pricing.html, reviewed in the app (http://localhost:5173/pricing) on " in text
    assert "`paper` #fbf7f0 → removed; `brand` #1f5c4a → #2563eb." in text
    # A write of anything but a theme file is not looked at.
    (tmp_path / "src").mkdir()
    other = tmp_path / "src" / "App.tsx"
    other.write_text("export {}", encoding="utf-8")
    assert find_drift(tmp_path, [other]) == []


def test_the_write_that_moves_it_is_told_in_its_result_and_logged(tmp_path):
    _project(tmp_path)
    _review(tmp_path)
    registry = ToolRegistry()

    def write_file(path: str = "", content: str = "") -> str:
        (tmp_path / path).write_text(content, encoding="utf-8")
        return f"Wrote {path}"

    registry.register(write_file)
    provider = _Scripted([
        AssistantTurn(tool_calls=[ToolCall(id="w1", name="write_file", arguments={
            "path": "tailwind.config.js", "content": _CONFIG.replace("#1f5c4a", "#2563eb")})], finish_reason="tool_calls"),
    ])
    engine = TurnEngine(provider=provider, registry=registry, permissions=PermissionEngine(workspace_root=tmp_path),
                        model="gpt-5.5", approver=_approve)
    engine.log = SessionLog(tmp_path / "session.jsonl")
    engine.design_checker = lambda *args, **kwargs: None
    engine.design_drift = lambda paths: find_drift(tmp_path, paths)

    async def go():
        return [event async for event in engine.run("make the button blue on settings")]

    asyncio.run(go())
    result = next(m for m in engine.messages if m.get("role") == "tool")
    assert result["content"].startswith("Wrote tailwind.config.js")
    assert "[design drift]" in result["content"] and "`brand` #1f5c4a → #2563eb" in result["content"]
    rows = [row for row in engine.log.read() if row["type"] == "design/drift"]
    assert len(rows) == 1 and rows[0]["pages"] == ["design/pricing.html"] and rows[0]["tokens"] == 1
    assert any("[design drift]" in str(m.get("content")) for m in provider.seen[1] if m)
    json.dumps(rows[0])
