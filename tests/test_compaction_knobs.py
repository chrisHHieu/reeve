"""The 2026 compaction round: resume hints and a fallback that is not silence.

Each of these closes a gap named by the external review (docs/compaction-best-practices.md):
Resuming with the most recently accessed files; a fallback summary carries
failures and last-dropped turns while ours said only 'no summary available'.

The trigger itself is `window − max(output, buffer)`; the knobs are the buffer
and the recent-token keep. The old per-model threshold fractions are gone.
"""

from __future__ import annotations

import json

from harness.context.compaction import extract_working_state
from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.interface.server.manager import SessionManager
from harness.tools import ToolRegistry


def _call(name, args, result=None, call_id="c1"):
    return (
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result or {})},
    )


# -- the resume hint ----------------------------------------------------------------


def test_recently_touched_files_appear_reads_included_writes_not_duplicated():
    read, read_result = _call(
        "read_file", {"path": "/proj/src/late.py"}, {"content": "ok"}, call_id="c1"
    )
    write, write_result = _call(
        "write_file", {"path": "/proj/src/early.py"}, {"ok": True}, call_id="c2"
    )
    state = extract_working_state([read, read_result, write, write_result])
    assert "Last files touched" in state
    assert "/proj/src/late.py" in state  # the read: only reachable through the hint
    # The write already has its own section — the hint does not list it twice.
    touched_block = state.split("Last files touched")[1]
    assert "/proj/src/early.py" not in touched_block


def test_the_hint_is_capped_at_five():
    messages = []
    for i in range(8):
        call, result = _call(
            "read_file", {"path": f"/proj/f{i}.py"}, {}, call_id=f"c{i}"
        )
        messages += [call, result]
    state = extract_working_state(messages)
    touched = state.split("Last files touched")[1]
    assert "/proj/f7.py" in touched  # most recent first
    assert "/proj/f2.py" not in touched  # beyond the five


# -- the no-summary fallback ---------------------------------------------------------


# -- settings plumbing ---------------------------------------------------------------


def test_manager_persists_and_validates_the_buffer_and_keep(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")

    assert manager.compaction_settings()["buffer_tokens"] == 20_000  # defaults
    assert manager.compaction_settings()["keep_tokens"] == 8_000
    assert manager.compaction_settings()["max_context_tokens"] == "auto"  # the default

    assert manager.set_compaction_settings(buffer_tokens="lots")["ok"] is False
    assert manager.set_compaction_settings(keep_tokens="lots")["ok"] is False
    assert manager.set_compaction_settings(max_context_tokens="lots")["ok"] is False

    result = manager.set_compaction_settings(
        buffer_tokens=30_000, keep_tokens=12_000, max_context_tokens=250_000
    )
    assert result["ok"] is True
    settings = manager.compaction_settings()
    assert settings["buffer_tokens"] == 30_000 and settings["keep_tokens"] == 12_000
    assert settings["max_context_tokens"] == 250_000
    # Clamped, never negative.
    assert manager.set_compaction_settings(buffer_tokens=-5)["buffer_tokens"] == 0
    payload = manager.compaction_settings_payload()
    assert payload["compaction_buffer_tokens"] == 0
    assert payload["compaction_keep_tokens"] == 12_000
    assert payload["compaction_max_context_tokens"] == 250_000

    # The default mode is a string, and it round-trips; junk strings are refused
    # without clearing a previously stored cap.
    assert (
        manager.set_compaction_settings(max_context_tokens="auto")[
            "max_context_tokens"
        ]
        == "auto"
    )
    assert (
        manager.compaction_settings_payload()["compaction_max_context_tokens"] == "auto"
    )
    assert manager.set_compaction_settings(max_context_tokens="lots")["ok"] is False
    assert manager.compaction_settings()["max_context_tokens"] == "auto"
    assert (
        manager.set_compaction_settings(max_context_tokens=180_000)[
            "max_context_tokens"
        ]
        == 180_000
    )

    # Custom summarizer instructions round-trip, bounded to 8k chars.
    assert manager.compaction_settings()["instructions"] == ""
    assert (
        manager.set_compaction_settings(instructions="Keep only ticket ids.")[
            "instructions"
        ]
        == "Keep only ticket ids."
    )
    assert (
        manager.compaction_settings_payload()["compaction_instructions"]
        == "Keep only ticket ids."
    )
    assert (
        len(manager.set_compaction_settings(instructions="x" * 9_000)["instructions"])
        == 8_000
    )


def test_compaction_config_table_wins_over_legacy_prefs(tmp_path, monkeypatch):
    """Compaction is configured in config.toml `[compaction]`.
    A key the file names beats the legacy pref the removed Settings card used to write;
    a key the file does not name still falls back to the pref, then the default. A
    workspace file overrides the global one."""
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "config.toml").write_text(
        "[compaction]\n"
        "buffer_tokens = 12345\n"
        "keep_tokens = 6789\n"
        'max_context_tokens = 180000\n'
        'model = "gpt-4o-mini"\n'
        'instructions = "Only ticket ids."\n',
        encoding="utf-8",
    )
    manager = SessionManager(data_dir=tmp_path / "data")
    # Legacy prefs exist for every knob (what the old card would have written).
    manager.set_compaction_settings(
        buffer_tokens=1,
        keep_tokens=2,
        max_context_tokens=3,
        model="legacy-model",
        instructions="legacy instructions",
    )
    settings = manager.compaction_settings()
    assert settings["buffer_tokens"] == 12_345
    assert settings["keep_tokens"] == 6_789
    assert settings["max_context_tokens"] == 180_000
    assert settings["model"] == "gpt-4o-mini"
    assert settings["instructions"] == "Only ticket ids."

    # A key the file does not name falls back to the legacy pref.
    (tmp_path / "state" / "config.toml").write_text(
        "[compaction]\nbuffer_tokens = 555\n", encoding="utf-8"
    )
    manager._compaction_tables.clear()
    settings = manager.compaction_settings()
    assert settings["buffer_tokens"] == 555
    assert settings["keep_tokens"] == 2  # legacy pref, not the default

    # Workspace `.reeve/config.toml` overrides the global table for that project.
    ws = tmp_path / "proj"
    (ws / ".reeve").mkdir(parents=True)
    (ws / ".reeve" / "config.toml").write_text(
        "[compaction]\nbuffer_tokens = 777\n", encoding="utf-8"
    )
    assert manager.compaction_settings(ws)["buffer_tokens"] == 777
    assert manager.compaction_settings()["buffer_tokens"] == 555


def test_keep_tokens_zero_keeps_the_newest_step():
    """`0` is not "keep nothing": every non-empty suffix exceeds a zero budget, so the
    documented fallback applies — the newest assistant step, or the newest user turn when
    there is none (`harness/context/compaction/state.pick_boundary`)."""
    from harness.context.compaction import pick_boundary

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "two"},
        {"role": "assistant", "content": "a2"},
    ]
    assert pick_boundary(messages, keep_tokens=0) == 4  # the newest assistant step
    assert pick_boundary(messages[:4], keep_tokens=0) == 3  # newest user turn, nothing after


def test_engine_config_carries_the_buffer_and_keep(tmp_path):
    engine = TurnEngine(
        provider=object(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )
    engine.compaction_settings = lambda: {
        "buffer_tokens": 40_000,
        "keep_tokens": 5_000,
        "context_window": 1_000_000,
    }
    assert engine._compaction_config()["buffer_tokens"] == 40_000
    assert engine._history_trigger_tokens() == 960_000


def test_engine_config_reserves_the_output_budget_for_the_trigger(tmp_path):
    """The request's own `max_tokens` becomes the trigger's output reserve; the buffer is
    the floor, so `max(output, buffer)` decides how much room the next answer gets."""
    engine = TurnEngine(
        provider=object(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="some-small-window-model",
        model_settings={"max_tokens": 8_000},
    )
    engine.compaction_settings = lambda: {
        "context_window": 32_768,
    }
    assert engine._compaction_config()["output_tokens"] == 8_000
    # max(8_000 output, 20_000 buffer) = 20_000, so the trigger is 12_768.
    assert engine._history_trigger_tokens() == 12_768

    engine._last_context_tokens = 12_768
    engine._context_measured_at = len(engine.messages)
    assert engine._compaction_due() is True


def test_engine_config_uses_the_whole_window_of_a_big_model(tmp_path):
    engine = TurnEngine(
        provider=object(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="big-window-model",
    )
    engine.compaction_settings = lambda: {
        "context_window": 1_000_000,
    }
    engine._context_measured_at = len(engine.messages)
    assert engine._history_trigger_tokens() == 980_000  # no 250k cap any more
    engine._last_context_tokens = 500_000
    assert engine._compaction_due() is False
    engine._last_context_tokens = 985_000
    assert engine._compaction_due() is True


def test_the_context_cap_bounds_any_models_window(tmp_path):
    """`max_context_tokens` is the one knob a person has: whatever the catalogue
    declares, the trigger is computed from the capped window. 0 leaves the window alone,
    and a cap larger than the window is a no-op."""
    engine = TurnEngine(
        provider=object(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="big-window-model",
    )
    engine.compaction_settings = lambda: {
        "context_window": 1_000_000,
        "max_context_tokens": 250_000,
    }
    assert engine._compaction_config()["context_window"] == 250_000
    assert engine._history_trigger_tokens() == 230_000
    engine._context_measured_at = len(engine.messages)
    engine._last_context_tokens = 229_999
    assert engine._compaction_due() is False
    engine._last_context_tokens = 230_000
    assert engine._compaction_due() is True

    # A cap larger than the model's own window changes nothing.
    engine.compaction_settings = lambda: {
        "context_window": 128_000,
        "max_context_tokens": 250_000,
    }
    assert engine._compaction_config()["context_window"] == 128_000
    assert engine._history_trigger_tokens() == 108_000

    # 0 (the default) keeps the window as the only bound.
    engine.compaction_settings = lambda: {
        "context_window": 1_000_000,
        "max_context_tokens": 0,
    }
    assert engine._history_trigger_tokens() == 980_000

    # Junk input cannot move the window.
    engine.compaction_settings = lambda: {
        "context_window": 1_000_000,
        "max_context_tokens": "lots",
    }
    assert engine._history_trigger_tokens() == 980_000


def test_auto_is_the_models_window_up_to_the_ceiling(tmp_path):
    """`max_context_tokens: "auto"` is one rule for every model: its own
    window, never more than 400k, and a capped window scales the verbatim keep."""
    engine = TurnEngine(
        provider=object(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="any-model",
    )
    engine.compaction_settings = lambda: {
        "context_window": 1_000_000,
        "max_context_tokens": "auto",
    }
    cfg = engine._compaction_config()
    assert cfg["context_window"] == 400_000
    assert cfg["keep_tokens"] == 40_000
    assert engine._history_trigger_tokens() == 380_000

    # A model whose window is already inside the ceiling keeps it, and its own keep.
    engine.compaction_settings = lambda: {
        "context_window": 200_000,
        "max_context_tokens": "auto",
    }
    cfg = engine._compaction_config()
    assert cfg["context_window"] == 200_000 and "uncapped_window" not in cfg
    assert cfg["keep_tokens"] == 8_000
    assert engine._history_trigger_tokens() == 180_000


def test_what_a_call_did_is_read_from_its_class_not_its_name():
    """"create" in a name made a connector call a file write, and a patch, which names
    no `path`, was never listed as one."""
    patch = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-a\n+b\n*** End Patch"
    calls = [
        *_call("apply_patch", {"patch": patch}, {"ok": True}, call_id="p1"),
        *_call("trello_create_card", {"title": "Ship it", "path": "/not/a/file"}, {"ok": True}, call_id="t1"),
    ]
    state = extract_working_state(calls)
    section = state.split("Files written/edited", 1)[1].splitlines()[1:]
    written = "\n".join(line for line in section[: next((i for i, line in enumerate(section) if not line.startswith("- ")), len(section))])
    assert "src/app.py" in written and "/not/a/file" not in written
    assert "trello_create_card: /not/a/file" in state or "trello_create_card: Ship it" in state
