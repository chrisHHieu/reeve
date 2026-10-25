"""The terminal interface: one conversation with the engine, run in this process.

Events are written to a scrolling log as they arrive (`transcript.lines_for`), an approval opens a
modal (`approval.ApprovalScreen`), and a line starting with `/` is a command (`commands.py`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, RichLog

from harness.compose import build_engine
from harness.llm.providers import ProviderClient
from harness.loop import ApprovalOutcome, PermissionRequest
from harness.modes.agents import reeve_agent
from harness.safety.permissions import Mode
from harness.session.conversations import ConversationStore
from harness.session.events import Event
from harness.session.sessions import SessionRecord

from .approval import ApprovalScreen
from .commands import _YOU, CommandsMixin
from .transcript import lines_for

_LAYOUT = """
#log { padding: 0 1; border: round $primary 30%; }
#prompt { dock: bottom; }
#approval { width: 80%; padding: 1 2; background: $panel; border: thick $warning; }
#approval-title { color: $warning; text-style: bold; }
#approval-buttons { padding-top: 1; height: auto; }
#approval-buttons Button { margin-right: 1; }
"""


class ReeveApp(CommandsMixin, App):
    CSS = _LAYOUT
    BINDINGS = [Binding("ctrl+c", "quit", "Quit"), Binding("escape", "interrupt", "Interrupt")]

    def __init__(
        self,
        *,
        workspace: str | Path,
        model: str = "gpt-5.6-sol",
        mode: Mode = Mode.WORKSPACE_WRITE,
        plan: bool = False,
        provider: Optional[ProviderClient] = None,
        user_rules: str = "",
        session_store: Optional[ConversationStore] = None,
        session_id: Optional[str] = None,
        resume_messages: Optional[list[dict]] = None,
    ) -> None:
        super().__init__()
        self.workspace = Path(workspace).expanduser().resolve()
        self.model, self.mode, self.plan = model, mode, plan
        self._engine_inputs = {"provider": provider, "user_rules": user_rules}
        self._store, self._session_id, self._resumed = session_store, session_id, resume_messages
        self.engine = None
        # Every line written, as text: what a test reads, since the log widget is not text.
        self.rendered: list[str] = []
        # Visuals the session showed, by name, so a later mention can be written out.
        self._visuals: dict = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="log", wrap=True, markup=True, highlight=False)
        yield Input(placeholder="Ask anything…   (/help for commands)", id="prompt")
        yield Footer()

    def _build(self, messages: Optional[list[dict]] = None):
        """An engine for this conversation; `/reload-plugins` and `/clear` build a new one."""
        return build_engine(
            agent=reeve_agent(),
            workspace=self.workspace,
            model=self.model,
            mode=self.mode,
            plan=self.plan,
            approver=self._approve,
            messages=messages,
            **self._engine_inputs,
        )

    def on_mount(self) -> None:
        self.engine = self._build(self._resumed)
        plan = "  ·  plan" if self.plan else ""
        self._write(f"[b]reeve[/b]  ·  model {self.model}  ·  mode {self.mode.value}{plan}")
        self._write(f"workspace: {self.workspace}")
        if self._resumed:
            self._write(f"[dim]picked up session {self._session_id}, {len(self._resumed)} messages[/dim]")
        self._write("Write what you need, or /help for the commands.\n")
        self.query_one("#prompt", Input).focus()

    async def _approve(self, request: PermissionRequest) -> ApprovalOutcome:
        return await self.push_screen_wait(ApprovalScreen(request))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = self.query_one("#prompt", Input)
        text, prompt.value = event.value.strip(), ""
        if text.startswith("/"):
            self._handle_command(text)
        elif text:
            self._write(f"{_YOU}{text}")
            self.run_turn(text)

    @work(exclusive=True)
    async def run_turn(self, text: str, display: Optional[str] = None) -> None:
        assert self.engine is not None
        try:
            async for event in self.engine.run(text, **({"display": display} if display else {})):
                self._render_event(event)
        except Exception as exc:  # pragma: no cover - shown in the log, not raised
            self._write(f"[red]error:[/red] {exc}")
        self._save()

    def _save(self) -> None:
        """Write the conversation to the store, when it has one. Mode and plan are read off the
        engine, which `/mode` and `/plan` change, so the record says what the turn really ran under."""
        if self._store is None or self.engine is None or not self._session_id:
            return
        permissions = self.engine.permissions
        self._store.save(
            SessionRecord(
                session_id=self._session_id,
                workspace=str(self.workspace),
                model=self.model,
                mode=permissions.mode.value,
                plan=bool(permissions.plan),
                messages=self.engine.messages,
            )
        )

    def _render_event(self, event: Event) -> None:
        for line in lines_for(event, self._visuals):
            self._write(line)

    def _write(self, text: str) -> None:
        self.rendered.append(text)
        if self.is_running:
            self.query_one("#log", RichLog).write(text)

    def action_interrupt(self) -> None:
        if self.engine:
            self.engine.request_interrupt()

    def action_quit(self) -> None:  # type: ignore[override]
        executor = self.engine and getattr(self.engine, "executor", None)
        if executor:
            executor.close()
        self.exit()
