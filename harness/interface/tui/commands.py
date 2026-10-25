"""The slash commands of the TUI, as a mixin over the app that owns the engine and the log."""

from __future__ import annotations

from textual.widgets import RichLog

from harness.safety.permissions import Mode

_HELP = (
    "commands: /mode read-only|workspace-write|danger-full-access · "
    "/plan [off|<objective>] · /model <id> · /clear · /plugins [enable|disable <id>] · "
    "/reload-plugins · /<command> [args] · /quit"
)
_YOU = "[b cyan]you[/b cyan] › "


class CommandsMixin:
    """Expects `engine`, `mode`, `plan`, `model`, `workspace`, `_write`, `_build` and `run_turn`."""

    def _handle_command(self, command: str) -> None:
        words = command.split()
        name, rest = words[0], words[1:]
        handler = {
            "/quit": self._cmd_quit,
            "/exit": self._cmd_quit,
            "/help": self._cmd_help,
            "/plugins": self._plugins_command,
            "/reload-plugins": self._cmd_reload_plugins,
            "/clear": self._cmd_clear,
        }.get(name)
        if handler is not None:
            handler(rest)
        elif name == "/mode" and rest:
            self._cmd_mode(rest[0])
        elif name == "/plan":
            self._cmd_plan(command.split(maxsplit=1)[1].strip() if rest else "")
        elif name == "/model" and rest:
            self._cmd_model(rest[0])
        elif not self._run_defined_command(command):
            self._write(f"[red]unknown command:[/red] {command}")

    def _cmd_quit(self, _rest: list[str]) -> None:
        self.exit()

    def _cmd_help(self, _rest: list[str]) -> None:
        self._write(_HELP)

    def _cmd_mode(self, spelling: str) -> None:
        # The three presets; older spellings still resolve through the Mode enum's migration map.
        try:
            chosen = Mode(spelling)
        except ValueError:
            self._write(f"[red]unknown mode:[/red] {spelling}")
            return
        self.mode = chosen
        if self.engine:
            self.engine.set_mode(chosen)
        self._write(f"mode → {chosen.value}")

    def _set_plan(self, on: bool) -> None:
        # `set_plan` is the engine's one live write path, so the toggle lands in the session log.
        self.plan = on
        if self.engine:
            self.engine.set_plan(on)

    def _cmd_plan(self, objective: str) -> None:
        """Plan is an overlay: bare toggles it, `off` clears it, and any other text switches it on
        and runs as an ordinary turn message in the same command."""
        if objective == "off":
            self._set_plan(False)
            self._write("plan mode off")
        elif objective:
            self._set_plan(True)
            self._write("plan mode on")
            self._write(f"{_YOU}{objective}")
            self.run_turn(objective)
        else:
            self._set_plan(not self.plan)
            self._write(f"plan mode {'on' if self.plan else 'off'}")

    def _cmd_model(self, model: str) -> None:
        self.model = model
        if self.engine:
            self.engine.model = model
        self._write(f"model → {model}")

    def _cmd_reload_plugins(self, _rest: list[str]) -> None:
        # Plugins are fixed when the engine is built; this rebuilds it over the same conversation,
        # so the next reply re-reads the whole history.
        messages = list(self.engine.messages) if self.engine else None
        self.engine = self._build(messages)
        self._write("plugins reloaded — the next reply re-reads the whole conversation")

    def _cmd_clear(self, _rest: list[str]) -> None:
        if self.engine:
            self.engine.messages = []
            self.engine = self._build()
        self.query_one("#log", RichLog).clear()
        self.rendered.clear()
        self._write("conversation cleared")

    def _plugins_command(self, args: list[str]) -> None:
        """`/plugins`: what loads here, what is installed but off, what failed. `enable`/`disable
        <id>` record the person's own choice; installing needs the consent screen, so it stays with
        the app and `reeve plugin install`."""
        from harness.extensions.plugins import resolve_plugins, store

        if args and args[0] in ("enable", "disable") and len(args) > 1:
            try:
                store.set_enabled(args[1], args[0] == "enable", "user")
            except ValueError as exc:
                self._write(f"[red]{exc}[/red]")
                return
            self._write(f"{args[0]}d {args[1]} — /reload-plugins to use it in this conversation")
            return
        if args:
            self._write("[red]usage:[/red] /plugins [enable|disable <id>]")
            return
        found = resolve_plugins(workspace=self.workspace)
        loaded = {plugin.id for plugin in found.plugins}
        code_notes = {"running": " · runs code", "needs_consent": " · code waits for your yes"}
        for plugin in found.plugins:
            parts = ", ".join(f"{count} {kind}" for kind, count in plugin.components.items()) or "nothing to load"
            self._write(
                f"  {plugin.id} {plugin.version or ''} · {plugin.scope} · {parts}{code_notes.get(plugin.code, '')}"
            )
        for row in store.list_installed(str(self.workspace)):
            if row["id"] not in loaded and not row["enabled"]:
                self._write(f"  [dim]{row['id']} · off[/dim]")
        for failure in found.errors:
            self._write(f"  [red]{failure['id']}: {failure['error']}[/red]")
        if not found.plugins and not found.errors:
            self._write("no plugins — install one with `reeve plugin install` or in the app")

    def _run_defined_command(self, command: str) -> bool:
        """A command a person or a plugin defined (`/ship 1.2`, `/shipit:deploy prod`): its body
        with the arguments filled in, sent as an ordinary message."""
        from harness.extensions.commands import expand, load_commands

        name, _, args = command[1:].partition(" ")
        spec = load_commands(self.workspace).get(name)
        if spec is None:
            return False
        self._write(f"{_YOU}{command}")
        self.run_turn(expand(spec.body, args.strip()), display=command)
        return True
