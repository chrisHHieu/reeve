"""`commands` in its object form (design §5.2): a plugin names each command, with
its body in a file of the plugin (`source`) or written inline (`content`), and metadata
that wins over the file's frontmatter. Nothing a command declares grants anything."""

from __future__ import annotations

import json
from pathlib import Path

from harness.extensions.commands import load_commands
from harness.extensions.plugins import resolve_plugins


def _plugin(tmp_path: Path, commands: object) -> Path:
    root = tmp_path / "shipit"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "shipit", "commands": commands}))
    (root / "docs").mkdir()
    (root / "docs" / "deploy.md").write_text(
        "---\ndescription: from the file\nargument-hint: <env>\n---\nDeploy to $1 from ${CLAUDE_PLUGIN_ROOT}/docs.\n"
    )
    return root


def test_named_commands_load_from_a_file_or_inline_with_the_object_s_metadata(tmp_path, monkeypatch):
    root = _plugin(
        tmp_path,
        {
            "deploy": {"source": "./docs/deploy.md", "description": "Deploy the app", "allowedTools": ["Bash(git:*)"]},
            "hello": {"content": "Say hello to $ARGUMENTS", "argumentHint": "<name>", "model": "sonnet"},
        },
    )
    monkeypatch.setenv("REEVE_PLUGIN_DIRS", str(root))
    plugin = resolve_plugins(workspace=tmp_path).plugins[0]
    assert plugin.components == {"commands": 2} and plugin.skipped == []

    found = load_commands(tmp_path)
    deploy, hello = found["shipit:deploy"], found["shipit:hello"]
    assert deploy.description == "Deploy the app"  # the object wins over the frontmatter
    assert deploy.argument_hint == "<env>"  # what the object leaves out, the file still gives
    assert deploy.allowed_tools == ("Bash(git:*)",)  # shown, never applied
    assert deploy.body == f"Deploy to $1 from {root.resolve().as_posix()}/docs."
    assert (hello.body, hello.argument_hint, hello.model, hello.plugin) == (
        "Say hello to $ARGUMENTS",
        "<name>",
        "sonnet",
        "shipit@inline",
    )


def test_a_command_that_cannot_be_read_is_left_out_and_noted(tmp_path, monkeypatch):
    root = _plugin(
        tmp_path,
        {
            "both": {"source": "./docs/deploy.md", "content": "x"},
            "neither": {"description": "nothing to run"},
            "outside": {"source": "../secrets.md"},
            "Bad Name": {"content": "x"},
            "missing": {"source": "./docs/none.md"},
            "ok": {"content": "fine"},
        },
    )
    monkeypatch.setenv("REEVE_PLUGIN_DIRS", str(root))
    plugin = resolve_plugins(workspace=tmp_path).plugins[0]
    assert plugin.components == {"commands": 1}
    notes = " | ".join(plugin.skipped)
    for expected in (
        "commands.both: needs exactly one of `source` or `content`",
        "commands.neither: needs exactly one",
        "commands.outside: ../secrets.md",
        "commands.Bad Name: not a command name",
        "commands.missing: ./docs/none.md does not exist",
    ):
        assert expected in notes
    assert list(load_commands(tmp_path)) == ["shipit:ok"]
