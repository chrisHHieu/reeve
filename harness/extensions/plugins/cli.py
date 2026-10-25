"""`reeve plugin …` (design §11.2).

The same functions the GUI calls. An install or an update prints what the plugin is and
asks y/N; `--yes` is accepted only for a plugin with nothing that runs code, so a script
can never consent to running code on a person's behalf.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Optional


def _print_consent(consent: dict[str, Any], out: Any = None) -> None:
    # Resolved per call: a default of `sys.stdout` binds the stream that existed when the
    # module loaded, and writes fail once that stream is replaced and closed.
    out = out or sys.stdout
    print(f"{consent.get('display_name') or consent['name']} {consent.get('version') or ''}".rstrip(), file=out)
    if consent.get("author"):
        print(f"  by {consent['author']}", file=out)
    if consent.get("source"):
        print(f"  from {consent['source']}" + (f" @ {consent['sha'][:12]}" if consent.get("sha") else ""), file=out)
    for key in ("skills", "commands", "agents"):
        if consent.get(key):
            print(f"  {key}: {', '.join(consent[key])}", file=out)
    for item in consent.get("executable") or []:
        print(f"  RUNS CODE ({item['kind']}):\n    " + item["detail"].replace("\n", "\n    "), file=out)
    for note in consent.get("skipped") or []:
        print(f"  not loaded: {note}", file=out)
    for option in consent.get("user_config") or []:
        flag = " (secret)" if option.get("sensitive") else ""
        print(f"  asks for: {option['title']}{flag}", file=out)
    for dep in consent.get("dependencies") or []:
        print(f"  needs: {dep['id']}" + ("" if dep.get("installed") else " (installs with it)"), file=out)


def _confirm(consent: dict[str, Any], yes: bool, ask: Callable[[str], str]) -> bool:
    runs_code = bool(consent.get("executable")) or any(
        (d.get("consent") or {}).get("executable") for d in consent.get("dependencies") or []
    )
    if yes and runs_code:
        print("refusing --yes: this plugin runs code; confirm it yourself", file=sys.stderr)
        return False
    if yes:
        return True
    return ask("Install? [y/N] ").strip().lower() in ("y", "yes")


def main(argv: list[str], *, ask: Callable[[str], str] = input) -> int:
    from . import manager, marketplace, resolve_plugins, store
    from .manifest import read_manifest
    from .marketplace import PluginError

    parser = argparse.ArgumentParser(prog="reeve plugin", description="Plugins.")
    sub = parser.add_subparsers(dest="action", required=True)
    validate = sub.add_parser("validate", help="Check a plugin folder")
    validate.add_argument("dir")
    validate.add_argument("--strict", action="store_true", help="fail on anything left out")
    listing = sub.add_parser("list", help="What loads here")
    listing.add_argument("--json", action="store_true")
    install = sub.add_parser("install", help="Install <plugin>@<marketplace>")
    install.add_argument("id")
    install.add_argument("--scope", default="user", choices=["user", "project", "local"])
    install.add_argument("--workspace", default="")
    install.add_argument("--yes", action="store_true", help="only for a plugin that runs no code")
    for verb in ("enable", "disable"):
        cmd = sub.add_parser(verb)
        cmd.add_argument("id")
        cmd.add_argument("--scope", default="user", choices=["user", "project", "local"])
        cmd.add_argument("--workspace", default="")
    update = sub.add_parser("update", help="Update an installed plugin")
    update.add_argument("id")
    update.add_argument("--yes", action="store_true")
    remove = sub.add_parser("uninstall")
    remove.add_argument("id")
    remove.add_argument("--scope", default="user", choices=["user", "project", "local"])
    remove.add_argument("--workspace", default="")
    remove.add_argument("--keep-data", action="store_true")
    details = sub.add_parser("details")
    details.add_argument("id")
    keep = sub.add_parser("keep", help="Keep using a plugin its marketplace withdrew")
    keep.add_argument("id")
    market = sub.add_parser("marketplace", help="Marketplaces")
    market_sub = market.add_subparsers(dest="market_action", required=True)
    market_add = market_sub.add_parser("add")
    market_add.add_argument("source", help="owner/repo, a git URL, or a folder")
    market_sub.add_parser("list")
    market_update = market_sub.add_parser("update")
    market_update.add_argument("name", nargs="?")
    market_remove = market_sub.add_parser("remove")
    market_remove.add_argument("name")
    market_auto = market_sub.add_parser("auto-update", help="Let updates that run no new code apply themselves")
    market_auto.add_argument("name")
    market_auto.add_argument("state", choices=["on", "off"])
    args = parser.parse_args(argv)

    try:
        if args.action == "validate":
            return _validate(Path(args.dir).expanduser(), args.strict, read_manifest, resolve_plugins)
        if args.action == "list":
            plugins = resolve_plugins(workspace=Path.cwd())
            if args.json:
                print(json.dumps({**plugins.as_dict(), "installed": manager.list_installed(str(Path.cwd()))}, indent=1))
                return 0
            for p in plugins.plugins:
                parts = ", ".join(f"{n} {k}" for k, n in p.components.items()) or "nothing to load"
                print(f"{p.id}  {p.version or '-'}  {p.scope}  {parts}")
            for e in plugins.errors:
                print(f"error: {e['id']}: {e['error']}", file=sys.stderr)
            if not plugins.plugins and not plugins.errors:
                print("no plugins")
            return 1 if plugins.errors else 0
        if args.action == "install":
            staged = manager.stage_install(args.id, scope=args.scope, workspace=args.workspace or None)
            _print_consent(staged["consent"])
            if not _confirm(staged["consent"], args.yes, ask):
                manager.cancel_install(staged["token"])
                print("not installed")
                return 1
            done = manager.confirm_install(staged["token"])
            print(f"installed {done['id']} {done['version']} ({done['scope']})")
            return 0
        if args.action in ("enable", "disable"):
            store.set_enabled(args.id, args.action == "enable", args.scope, args.workspace or None)
            print(f"{args.action}d {args.id} ({args.scope})")
            return 0
        if args.action == "update":
            staged = manager.stage_update(args.id)
            if staged.get("up_to_date"):
                print(f"{args.id} is up to date ({staged['version']})")
                return 0
            _print_consent(staged["consent"])
            diff = staged.get("diff") or {}
            if diff.get("executable_changed"):
                print("  WHAT RUNS CODE CHANGED — read it again")
            if not _confirm(staged["consent"], args.yes, ask):
                manager.cancel_install(staged["token"])
                print("not updated")
                return 1
            done = manager.confirm_install(staged["token"])
            print(f"updated {done['id']} to {done['version']}")
            return 0
        if args.action == "uninstall":
            done = manager.uninstall(args.id, scope=args.scope, workspace=args.workspace or None, keep_data=args.keep_data)
            print(f"uninstalled {done['id']} ({done['scope']})" + (", data removed" if done["data_removed"] else ""))
            return 0
        if args.action == "details":
            record = store.install_record(args.id)
            if record is None:
                print(f"{args.id} is not installed", file=sys.stderr)
                return 1
            print(json.dumps({k: v for k, v in record.items() if k != "entry"}, indent=1))
            return 0
        if args.action == "keep":
            from .updates import keep_flagged

            keep_flagged(args.id)
            print(f"keeping {args.id}")
            return 0
        if args.market_action == "add":
            added = marketplace.add_marketplace(args.source)
            print(f"added {added['name']} ({added['plugins']} plugins)")
        elif args.market_action == "list":
            for m in marketplace.list_marketplaces():
                print(f"{m['name']}  {m['plugins']} plugins" + (f"  error: {m['error']}" if m["error"] else ""))
        elif args.market_action == "update":
            names = [args.name] if args.name else [m["name"] for m in marketplace.list_marketplaces()]
            for name in names:
                done = marketplace.refresh_marketplace(name)
                print(f"{name}: {done['plugins']} plugins")
                for plugin_id in done.get("flagged") or []:
                    print(f"  {plugin_id} is no longer listed — it stops loading (`reeve plugin keep {plugin_id}` to keep it)")
        elif args.market_action == "auto-update":
            from .updates import set_auto_update

            set_auto_update(args.name, args.state == "on")
            print(f"auto-update {args.state} for {args.name}")
        else:
            marketplace.remove_marketplace(args.name)
            print(f"removed {args.name}")
        return 0
    except PluginError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _validate(folder: Path, strict: bool, read_manifest, resolve_plugins) -> int:
    """Design §8.8: 0 passes, 1 passes with warnings under --strict, 2 is an error."""
    manifest = read_manifest(folder)
    plugins = resolve_plugins([folder])
    print(f"plugin: {manifest.name}" + (f" {manifest.version}" if manifest.version else ""))
    print(f"manifest: {manifest.manifest_path or '(none — named after the folder)'}")
    for info in plugins.plugins:
        for key, count in info.components.items():
            print(f"  {key}: {count}")
    skipped = plugins.plugins[0].skipped if plugins.plugins else manifest.skipped
    for note in skipped:
        print(f"warning: {note}")
    for e in plugins.errors:
        print(f"error: {e['error']}", file=sys.stderr)
    if plugins.errors:
        return 2
    return 1 if strict and skipped else 0


def run(argv: Optional[list[str]] = None) -> int:
    return main(list(sys.argv[1:] if argv is None else argv))
