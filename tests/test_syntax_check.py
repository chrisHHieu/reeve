"""Post-edit syntax diagnostics (harness/tools/syntax.py + the engine hook).

The claim being tested: a file a write tool just produced that no longer parses is
reported to the model in the same step, with the position and the parser's own message —
and nothing else changes. A clean file, an unsupported format, a failed call and a read
all add nothing.
"""

from __future__ import annotations

import json

from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import ModelCapabilities, ProviderClient, ToolCall
from harness.llm.providers.base import AssistantTurn
from harness.tools.syntax import check_file, check_text, supports
from harness.tools import ToolRegistry


class Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path) -> TurnEngine:
    return TurnEngine(
        provider=Provider(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=[{"role": "system", "content": "s"}],
    )


# -- the checker -----------------------------------------------------------------


def test_a_broken_python_file_is_reported_with_its_position():
    note = check_text("def f(:\n    pass\n", name="app.py")
    assert note is not None and note.startswith("app.py:1:")
    assert "invalid syntax" in note


def test_only_formats_with_a_real_parser_are_checked():
    for good in ("x = 1\n", "{}", "key = 1\n", "a: 1\n"):
        assert check_text(good, name="f.py") is None  # parses fine
    broken = {
        "f.json": "{",
        "f.toml": "key = [",
        "f.yaml": "a: [1,\n",
    }
    for name, text in broken.items():
        assert check_text(text, name=name), name
    # No parser here means no claim: JS/Go/Rust are deliberately not guessed at.
    assert check_text("function ( {", name="app.ts") is None
    assert not supports("app.go") and supports("app.py")


def test_binary_content_is_not_parsed_as_a_file(tmp_path):
    target = tmp_path / "blob.json"
    target.write_bytes(b"\x00\x01\x02 not json at all")
    assert check_file(target) is None


def test_jsonc_configs_tolerate_comments_and_trailing_commas():
    """`pyrightconfig.json` and `tsconfig.json` are JSONC: pyright accepts comments while
    the strict check rejected them (a live session, 2026-09-19, had to strip a two-line
    comment to satisfy the hook). Other .json files stay strict."""
    config = (
        "// Resolve imports from the project's own venv.\n"
        "{\n"
        '  "venvPath": ".",\n'
        '  "venv": ".venv",\n'
        '  "include": ["app",],\n'
        "  /* a block comment */\n"
        '  "url": "https://example.com//not-a-comment",\n'
        '  "quoted": "a\\",b",\n'
        "}\n"
    )
    assert check_text(config, name="pyrightconfig.json") is None
    assert check_text(config, name="tsconfig.build.json") is None
    assert check_text(config, name="ws/.vscode/settings.json") is None
    # The same text in an ordinary .json is still a parse failure — the tolerance is
    # keyed to the file, not to the content.
    assert check_text(config, name="data.json") is not None
    # And JSONC tolerance does not excuse a genuinely broken document.
    assert check_text('{\n  "a": ,\n}', name="pyrightconfig.json") is not None


def test_missing_and_oversized_files_are_left_alone(tmp_path):
    assert check_file(tmp_path / "missing.py") is None
    big = tmp_path / "big.py"
    big.write_text("x = 1\n" * 400_000, encoding="utf-8")  # ~2.4 MB
    assert check_file(big) is None


# -- the engine hook --------------------------------------------------------------


def _write_call(name: str = "write_file", path: str = "app.py") -> ToolCall:
    return ToolCall(id="c1", name=name, arguments={"path": path, "content": "..."})


def test_a_broken_write_reaches_the_model_in_the_same_step(tmp_path):
    (tmp_path / "app.py").write_text("def f(:\n", encoding="utf-8")
    engine = _engine(tmp_path)
    engine._record_result(_write_call(), {"ok": True}, "ok")

    message = engine.messages[-1]
    assert message["role"] == "tool"
    assert "[syntax check]" in message["content"]
    assert "app.py:1:" in message["content"]


def test_a_clean_write_adds_nothing(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    engine = _engine(tmp_path)
    engine._record_result(_write_call(), {"ok": True}, "ok")
    assert "syntax" not in engine.messages[-1]["content"]


def test_a_failed_write_is_not_checked(tmp_path):
    (tmp_path / "app.py").write_text("def f(:\n", encoding="utf-8")
    engine = _engine(tmp_path)
    engine._record_result(_write_call(), {"error": "denied"}, "error")
    assert "syntax" not in engine.messages[-1]["content"]


def test_a_read_is_not_checked(tmp_path):
    (tmp_path / "app.py").write_text("def f(:\n", encoding="utf-8")
    engine = _engine(tmp_path)
    engine._record_result(
        ToolCall(id="c2", name="read_file", arguments={"path": "app.py"}),
        {"content": "def f(:"},
        "ok",
    )
    assert "syntax" not in engine.messages[-1]["content"]


def test_a_patch_tool_is_checked_through_its_headers(tmp_path):
    (tmp_path / "mod.json").write_text('{"a": 1,}\n', encoding="utf-8")
    engine = _engine(tmp_path)
    engine._record_result(
        ToolCall(
            id="c3",
            name="apply_patch",
            arguments={
                "patch": "*** Begin Patch\n*** Update File: mod.json\n"
                "{\"a\": 1}\n*** End Patch\n"
            },
        ),
        {"ok": True},
        "ok",
    )
    assert "[syntax check]" in engine.messages[-1]["content"]


def test_the_note_lands_in_a_dict_result_too(tmp_path):
    """A write tool that returns a dict gets the note under `syntax`, so the result's
    shape (and whatever the caller parses) is preserved."""
    (tmp_path / "conf.yaml").write_text("a: [1,\n", encoding="utf-8")
    engine = _engine(tmp_path)
    engine._record_result(_write_call(path="conf.yaml"), {"ok": True, "path": "conf.yaml"}, "ok")

    message = engine.messages[-1]
    payload = json.loads(message["content"])
    assert payload["ok"] is True and payload["path"] == "conf.yaml"
    assert payload["syntax"].startswith("[syntax check]")
