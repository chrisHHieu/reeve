"""`reeve`: with no command, the terminal interface on the current folder.

Three words before anything else are commands of their own: `reeve skill …` manages the same
skills the server and the app use (list them, install from a community repository; also a way to
see what discovery finds), `reeve plugin …` manages plugins, and `reeve exec …` runs one task with
no interface.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path
from typing import Callable, Optional

from harness.config.config import load_config
from harness.context.memory import MemorySettingsStore
from harness.safety.permissions import Mode
from harness.safety.secrets import database_path, state_dir
from harness.session.conversations import ConversationStore

# The three presets, then older names `Mode` still maps onto them.
_MODES = ["read-only", "workspace-write", "danger-full-access", "plan", "discuss", "interactive", "auto", "bypass-approvals", "auto-approve"]


def _skill_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reeve skill", description="Manage skills.")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="List installed skills")
    install = actions.add_parser("install", help="Install skills from a GitHub repo, git URL, or URL")
    install.add_argument("source", help="owner/repo, a git/https URL, or a subpath in a repo")
    install.add_argument("-s", "--skill", action="append", default=[], dest="skills", help="Install only this skill (repeatable)")
    install.add_argument("-a", "--agent", default="", help="Community agent target (default: universal — the shared .agents/skills folder)")
    install.add_argument("--preview", action="store_true", help="Stage and preview without installing")
    install.add_argument("--overwrite", action="store_true", help="Replace existing skills of the same name")
    return parser


def _list_skills(store) -> int:
    rows = store.rows()
    if not rows:
        print("No skills yet.")
        return 0
    sources = store.installed_map()
    for row in rows:
        origin = sources.get(row["name"], {}).get("source", "")
        print(f"{row['name']} [{row['scope']}]" + (f" from {origin}" if origin else ""))
        print(f"    {row['description'] or '(no description)'}")
    return 0


def _install_skills(store, args: argparse.Namespace) -> int:
    """The same two steps the app takes: stage and show what came, then confirm. `--preview`
    stops after showing, where the app's review screen would wait."""
    try:
        staged = store.stage_install(args.source, skills=args.skills, agent=args.agent)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    found = staged["skills"]
    print(f"{len(found)} skill(s) found from {staged['source']}:")
    for entry in found:
        print(f"  - {entry['name']} ({entry['description_length']}/500)")
        print(f"      {entry['description'] or '(no description)'}")
        if entry["blocked"]:
            print(f"      blocked: {entry['blocked']}")
    for warning in staged.get("warnings") or ():
        print(f"  warning: {warning}")
    if args.preview:
        store.discard_install(staged["token"])
        print(f"preview only — nothing installed ({len(found)} skill(s) staged)")
        return 0
    try:
        installed = store.confirm_install(staged["token"], overwrite=args.overwrite)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("installed: " + ", ".join(skill["name"] for skill in installed))
    return 0


def _skill_cli(argv: list[str]) -> int:
    """`reeve skill list` and `reeve skill install <source>`."""
    from harness.extensions.skills import SkillStore

    args = _skill_parser().parse_args(argv)
    store = SkillStore()
    return _list_skills(store) if args.action == "list" else _install_skills(store, args)


def _plugin_cli(argv: list[str]) -> int:
    """`reeve plugin …`, in `harness.extensions.plugins.cli`."""
    from harness.extensions.plugins.cli import main as plugin_main

    return plugin_main(argv)


def _exec_cli(argv: list[str]) -> int:
    from .exec_cli import main as exec_main

    return exec_main(argv)


_COMMANDS: dict[str, Callable[[list[str]], int]] = {"skill": _skill_cli, "plugin": _plugin_cli, "exec": _exec_cli}


def add_plugin_dirs(dirs: Optional[list[str]]) -> None:
    """`--plugin-dir`, given any number of times: these plugin folders are loaded for this run,
    before whatever `REEVE_PLUGIN_DIRS` already names."""
    if not dirs:
        return
    from harness.extensions.plugins import ENV_DIRS

    already = [folder for folder in os.environ.get(ENV_DIRS, "").split(os.pathsep) if folder]
    given = [str(Path(folder).expanduser().resolve()) for folder in dirs]
    os.environ[ENV_DIRS] = os.pathsep.join(dict.fromkeys(given + already))


def _tui_parser() -> argparse.ArgumentParser:
    settings = load_config()
    parser = argparse.ArgumentParser(prog="reeve", description="Reeve, a local-first coding agent (TUI).")
    # Accepted and not used: the word once picked a persona.
    parser.add_argument("skill", nargs="?", default="code", help="skill to launch (default: code)")
    parser.add_argument("--cwd", default=".", help="workspace directory")
    parser.add_argument("--plugin-dir", action="append", default=[], help="load a plugin folder for this run (repeatable)")
    parser.add_argument("--model", default=settings.model, help="model id, e.g. openai gpt-5.5")
    parser.add_argument("--mode", default=settings.mode, choices=_MODES, help="permission preset")
    parser.add_argument("--resume", default=None, help="resume a session id")
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    words = list(sys.argv[1:] if argv is None else argv)
    if words and words[0] in _COMMANDS:
        raise SystemExit(_COMMANDS[words[0]](words[1:]))

    args = _tui_parser().parse_args(argv)
    add_plugin_dirs(args.plugin_dir)
    workspace = Path(args.cwd).expanduser().resolve()
    # The same store and SOUL.md the app and server use: one place for every conversation.
    home = state_dir()
    conversations = ConversationStore(home, db_path=database_path(home))
    conversations.touch_workspace(os.path.realpath(str(workspace)))

    session_id = args.resume or uuid.uuid4().hex[:12]
    model, mode, plan, earlier = args.model, Mode(args.mode), False, None
    record = conversations.load(args.resume) if args.resume else None
    if record is not None:
        earlier, model = record.messages, record.model
        # An older stored mode maps onto the presets; one stored as `plan` was a mode then and
        # comes back as the plan overlay.
        mode, plan = Mode.migrated(record.mode)
        plan = plan or bool(getattr(record, "plan", False))

    from .tui.app import ReeveApp

    ReeveApp(
        workspace=workspace,
        model=model,
        mode=mode,
        plan=plan,
        user_rules=MemorySettingsStore(home / "memory-settings.json").prompt_rules(),
        session_store=conversations,
        session_id=session_id,
        resume_messages=earlier,
    ).run()


if __name__ == "__main__":
    main()
