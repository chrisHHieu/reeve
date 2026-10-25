"""ReviewsMixin — git changes and PDF attachment settings.
"""
from __future__ import annotations

import re
from harness.config.config import load_config
from harness.integrations.connectors import experimental_enabled
from pathlib import Path
from typing import Any, Optional

from harness.platform.spawn import run as spawn_run
from .untracked import MAX_INSPECTED, inspect_untracked, revision_of, untracked_content


class ReviewsMixin:
    # -- git changes (the Review tab) ---------------------------------
    def _change_root(self, session_id: str) -> Optional[Path]:
        """The session's primary workspace, when it is a directory on disk."""
        record = self.session_store.load(session_id)
        ws = (record.workspace if record else None) or self.default_workspace
        if not ws:
            return None
        root = Path(ws)
        return root if root.is_dir() else None

    @staticmethod
    def _git(root: Path, *args: str, timeout: float = 15.0) -> Optional[str]:
        """stdout of a git command in `root`, or None on any failure. Read-only callers only."""
        try:
            proc = spawn_run(
                ["git", "-C", str(root), *args],
                capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
            )
        except Exception:
            return None
        return proc.stdout if proc.returncode == 0 else None

    def session_changes(self, session_id: str) -> dict[str, Any]:
        """Working-tree changes for the session's workspace — the Review tab's list.

        Read-only: `status` + `numstat` only, never a write to the repo. A non-git
        workspace returns ``is_git: false`` and an empty list, so the surface can say so
        instead of pretending there are no changes.
        """
        root = self._change_root(session_id)
        if root is None:
            return {"is_git": False, "files": [], "workspace": None}
        inside = self._git(root, "rev-parse", "--is-inside-work-tree")
        if not inside or inside.strip() != "true":
            return {"is_git": False, "files": [], "workspace": str(root)}

        counts: dict[str, list[int]] = {}
        for args in (("diff", "--numstat"), ("diff", "--cached", "--numstat")):
            for line in (self._git(root, *args) or "").splitlines():
                parts = line.split("\t")
                if len(parts) != 3:
                    continue
                add, dele, path = parts
                slot = counts.setdefault(path, [0, 0])
                slot[0] += int(add) if add.isdigit() else 0
                slot[1] += int(dele) if dele.isdigit() else 0

        files: list[dict[str, Any]] = []
        inspected = 0
        for line in (self._git(root, "status", "--porcelain=v1", "-uall") or "").splitlines():
            if len(line) < 4:
                continue
            code, raw = line[:2], line[3:]
            path = raw.split(" -> ")[-1].strip().strip('"')
            if code == "??":
                state = "A"
            elif "R" in code:
                state = "R"
            elif "D" in code:
                state = "D"
            elif "A" in code:
                state = "A"
            else:
                state = "M"
            add, dele = counts.get(path, [0, 0])
            entry_extra: dict[str, Any] = {}
            if state != "D":
                # Facts Review needs to keep a heavy card closed and to read a diff again only when
                # its file moved. An untracked file has no numstat: its additions are
                # its lines, so a long one is not mistaken for an empty change.
                if code == "??" and inspected < MAX_INSPECTED:
                    inspected += 1
                    facts = inspect_untracked(root / path)
                    if facts is not None:
                        entry_extra = {"size": facts["size"], "revision": facts["revision"]}
                        if facts["binary"]:
                            entry_extra["binary"] = True
                        else:
                            add = facts.get("lines", add)
                elif code != "??":
                    revision = revision_of(root / path)
                    if revision is not None:
                        entry_extra = {"revision": revision}
            # The porcelain code is TWO letters: `X` is the index (staged) side, `Y` the
            # working tree. The Review tab groups by that distinction (the
            # Staged/Changes), so it is carried through instead of collapsed — `??` and a
            # blank `X` are both "not staged".
            staged = code[0] not in (" ", "?")
            files.append(
                {"path": path, "status": state, "add": add, "del": dele, "staged": staged, **entry_extra}
            )
        files.sort(key=lambda f: f["path"])
        return {"is_git": True, "files": files, "workspace": str(root)}

    def session_change_diff(self, session_id: str, path: str) -> dict[str, Any]:
        """One changed file's patch for the Review preview. Read-only.

        A tracked file returns its unified diff against HEAD; an untracked file has no
        HEAD entry, so its content is returned instead and the surface renders it as
        additions. The path is scoped to the workspace — an absolute or escaping path is
        refused, since this reads the user's disk.
        """
        root = self._change_root(session_id)
        rel = (path or "").strip()
        if root is None or not rel:
            return {"ok": False, "error": "no file"}
        p = Path(rel)
        # Windows treats "/etc/passwd" as drive-relative (is_absolute() is False), so the
        # guard also rejects a leading separator and a drive letter explicitly.
        if p.is_absolute() or rel[:1] in ("/", "\\") or re.match(r"^[A-Za-z]:", rel) or ".." in p.parts:
            return {"ok": False, "error": "invalid path"}
        patch = self._git(root, "diff", "HEAD", "--", rel)
        if patch is None:
            patch = self._git(root, "diff", "--", rel) or ""
        if patch.strip():
            return {"ok": True, "path": rel, "patch": patch, "untracked": False}
        # No git diff, so what the file IS: its content when it is text of a sane size, a preview
        # of the start when it is long, and only facts when it is binary.
        return {"ok": True, "path": rel, **untracked_content(root / rel)}

    def get_settings(self) -> dict[str, Any]:
        """Model-access + UI status. Never returns the key; `source` says where it comes from."""
        import os

        env_key = bool(os.environ.get("OPENAI_API_KEY"))
        stored = bool((self.secrets.get("provider:openai") or {}).get("api_key"))
        # Only surface models whose provider is actually configured — the composer picker
        # reflects exactly what's connected. The active default is always kept selectable
        # (it's hidden behind the "No model" state until a provider is connected anyway).
        # Ollama is keyless, so "configured" is meaningless there — its models show only
        # while a local Ollama answers (cached liveness probe).
        def _selectable(m: str) -> bool:
            provider = self._model_provider(m)
            if provider == "ollama":
                return self._ollama_alive()
            return self._provider_configured(provider)

        selectable = [m for m in self._curated_models() if _selectable(m)]
        if self.model not in selectable:
            selectable.insert(0, self.model)
        from harness.llm.providers.matrix import model_context_windows, model_labels

        return {
            "provider": "openai",
            "model": self.model,
            # The permission mode a new conversation starts in (Settings ▸ General).
            "default_mode": self.mode.value,
            "models": selectable,
            # Curated-matrix display names ({full id → "GLM-5.2 · via Together"}) so every
            # picker shows human labels; custom models absent here render their raw id.
            "model_labels": model_labels(),
            # {full id → context window in tokens}, verified matrix entries only —
            # drives the composer's context-fill meter (absent id → meter hides).
            "model_context_windows": model_context_windows(),
            "has_key": env_key or stored,
            # Provider-agnostic "can this default model actually run?" — true when the default
            # model's provider is configured (any provider, not just OpenAI). Drives the GUI's
            # "No model connected" composer chip and the onboarding Skip warning.
            "model_ready": self._provider_configured(self._model_provider(self.model)),
            "source": "env" if env_key else ("store" if stored else None),
            "onboarded": bool(self._prefs.get("onboarded")),
            "experimental_connectors": experimental_enabled(self.secrets),
            "sessions_peek": self.sessions_peek(),
            "sidebar_organize": self.sidebar_organize(),
            "context_bar": self.context_bar(),
            # Composer: show the compact agent picker (P4). OFF by default —
            # the "Show agent" toggle, the same
            # default. Persisted like the other prefs, so the choice survives restarts.
            "show_agent": self.show_agent(),
            # Progressive tool discovery: MCP tools leave the wire and go
            # behind the `tool_search`/`tool_call` bridge. Same next-build semantics.
            "tool_discovery": self.tool_discovery(),
            # The default shell and the ones this machine may make it ("" is the OS's own).
            "shell": self.default_shell(),
            "shells": self.shell_choices(),
            # When `show_visual` is offered. Same next-build semantics.
            "visuals": self.visuals(),
            # May a delegation call pick its own
            # provider/model? Drive the Subagents tab's toggle.
            "subagent_model_selection": self.subagent_model_selection(),
            "scratch_base": self._prefs.get("scratch_base")
            or self.DEFAULT_SCRATCH_BASE,
            # Real on-disk secrets location, so the UI shows the OS-native path instead of a
            # hardcoded POSIX one (Windows -> %APPDATA%\Reeve, macOS/Linux -> ~/.config).
            "secrets_path": str(self.secrets.path),
            **self.pdf_settings(),
            **self.compaction_settings_payload(),
        }

    DEFAULT_SESSIONS_PEEK = 5

    def sessions_peek(self) -> int:
        """How many sessions a sidebar group shows before "Show more" (owner ask, 2026-07-03)."""
        try:
            n = int(self._prefs.get("sessions_peek", self.DEFAULT_SESSIONS_PEEK))
        except (TypeError, ValueError):
            n = self.DEFAULT_SESSIONS_PEEK
        return max(1, min(n, 50))

    def set_sessions_peek(self, n: int) -> dict[str, Any]:
        try:
            self._prefs["sessions_peek"] = max(1, min(int(n), 50))
        except (TypeError, ValueError):
            return {"ok": False, "error": "sessions_peek must be a number"}
        self._save_prefs()
        return {"ok": True, "sessions_peek": self.sessions_peek()}

    SIDEBAR_ORGANIZE = ("project", "list")

    def sidebar_organize(self) -> str:
        """How the sidebar lays out the sessions that are not pinned or waiting: grouped by their
        folder ("project", the default) or as one list by time ("list"). Owner ask 2026-10-12."""
        value = self._prefs.get("sidebar_organize")
        return value if value in self.SIDEBAR_ORGANIZE else self.SIDEBAR_ORGANIZE[0]

    def set_sidebar_organize(self, value: Any) -> dict[str, Any]:
        if value not in self.SIDEBAR_ORGANIZE:
            return {"ok": False, "error": "sidebar_organize must be 'project' or 'list'"}
        self._prefs["sidebar_organize"] = value
        self._save_prefs()
        return {"ok": True, "sidebar_organize": value}

    def context_bar(self) -> bool:
        """Whether the composer shows the context-window fill bar. ON by default (owner call
        2026-09-25, reversing 2026-07-30): Settings lost its toggle on the belief
        that the default was already ON, which left the bar unreachable. The pref stays for
        API compatibility; OFF makes the chip state the session total. The popover keeps
        both numbers either way."""
        return bool(self._prefs.get("context_bar", True))

    def set_context_bar(self, shown: Any) -> dict[str, Any]:
        self._prefs["context_bar"] = bool(shown)
        self._save_prefs()
        return {"ok": True, "context_bar": self.context_bar()}

    # -- Composer agent picker (P4) --------------------------------
    # The "Show agent" toggle: off by default, persisted, and purely a client
    # affordance — the picker reads `GET /v1/agents` + `GET /v1/subagents`, so nothing
    # here touches session state.
    def show_agent(self) -> bool:
        """Whether the composer shows the compact agent picker. OFF by default."""
        return bool(self._prefs.get("show_agent", False))

    def set_show_agent(self, shown: Any) -> dict[str, Any]:
        self._prefs["show_agent"] = bool(shown)
        self._save_prefs()
        return {"ok": True, "show_agent": self.show_agent()}

    # -- Progressive tool discovery -----------------------------------
    # Prefs-backed and user-global, applied on the NEXT session build (the tool set is part of
    # the cached request prefix).
    # ON by default since the 2026-09-24 audit measured the cost of leaving it off (a
    # median request carried ~15k tokens of MCP schema, 52% of the tool payload) — and
    # "on" only defers when the payload is worth a search round-trip
    # (`discovery.DISCOVERY_MIN_CHARS`). An explicit `false` in prefs still turns it off.
    def tool_discovery(self) -> bool:
        """Whether deferred tools (MCP) sit behind the search bridge in native mode."""
        return bool(self._prefs.get("tool_discovery", True))

    def set_tool_discovery(self, enabled: Any) -> dict[str, Any]:
        self._prefs["tool_discovery"] = bool(enabled)
        self._save_prefs()
        return {"ok": True, "tool_discovery": self.tool_discovery()}

    # -- Visuals -----------------------------------------------------------
    # Prefs-backed and user-global, applied on the NEXT session build like `tool_discovery`:
    # `show_visual` and its description are part of the cached request prefix. "auto" by default.
    VISUALS = ("auto", "asked", "off")

    def visuals(self) -> str:
        """When the model may show a visual: on its judgement, only when asked, or never."""
        value = self._prefs.get("visuals", "auto")
        return value if value in self.VISUALS else "auto"

    def set_visuals(self, value: Any) -> dict[str, Any]:
        if value not in self.VISUALS:
            return {"ok": False, "error": f"visuals is one of {', '.join(self.VISUALS)}"}
        self._prefs["visuals"] = value
        self._save_prefs()
        return {"ok": True, "visuals": self.visuals()}

    # -- Subagent model selection -------------------------------------------------
    # Prefs-backed and user-global, applied on the NEXT session build like `tool_discovery`
    # (the `task` schema is part of the cached request prefix). ON by default: the
    # delegation route override shipped on. OFF means `task` neither advertises nor
    # accepts `provider`/`model`, `list_subagent_models` is not registered, and a CLI
    # child refuses a model override too.
    def subagent_model_selection(self) -> bool:
        """Whether a delegation call may pick its own LLM route."""
        return bool(self._prefs.get("subagent_model_selection", True))

    def set_subagent_model_selection(self, enabled: Any) -> dict[str, Any]:
        self._prefs["subagent_model_selection"] = bool(enabled)
        self._save_prefs()
        return {
            "ok": True,
            "subagent_model_selection": self.subagent_model_selection(),
        }

    # -- PDF attachments / token savings (owner ask, 2026-07-17) ----------------
    DEFAULT_PDF_MAX_PAGES = 20
    DEFAULT_PDF_MAX_MB = 10

    def pdf_settings(self) -> dict[str, Any]:
        """Fallback mode for models without native PDF support + the attach-time
        thresholds (Settings → Token savings: big PDFs quietly eat tokens)."""
        from harness.llm.pdf_support import FALLBACK_MODES

        mode = self._prefs.get("pdf_fallback")
        try:
            pages = int(self._prefs.get("pdf_max_pages", self.DEFAULT_PDF_MAX_PAGES))
        except (TypeError, ValueError):
            pages = self.DEFAULT_PDF_MAX_PAGES
        try:
            mb = int(self._prefs.get("pdf_max_mb", self.DEFAULT_PDF_MAX_MB))
        except (TypeError, ValueError):
            mb = self.DEFAULT_PDF_MAX_MB
        return {
            "pdf_fallback": mode if mode in FALLBACK_MODES else "text",
            "pdf_max_pages": max(1, min(pages, 100)),
            "pdf_max_mb": max(1, min(mb, 10)),
        }

    def _compaction_table(self, workspace: Optional[str | Path] = None) -> dict[str, Any]:
        """The `[compaction]` table from config.toml for a workspace, snapshotted.

        Cached per workspace because the engine's due-check asks per step: re-reading TOML
        there would put file I/O on the hot path. A file edit applies to sessions built
        after it — the same contract every other config table has.
        """
        key = str(Path(workspace).expanduser()) if workspace else ""
        cached = self._compaction_tables.get(key)
        if cached is None:
            try:
                table = load_config(workspace).compaction
            except Exception:
                table = {}
            cached = dict(table) if isinstance(table, dict) else {}
            self._compaction_tables[key] = cached
        return cached

    def compaction_settings(
        self, workspace: Optional[str | Path] = None
    ) -> dict[str, Any]:
        """The auto-compaction knobs. File-first: `config.toml`
        `[compaction]` wins, then the legacy pref (what the removed Settings card wrote)
        for a key the file does not name, then the built-in default. The engine reads this
        per check, so the table is snapshotted once per workspace.

        Keys: `buffer_tokens` (the safety margin kept for the next answer, floor of
        `window − max(output, buffer)`), `keep_tokens` (recent history kept verbatim),
        `max_context_tokens` (the working window: a number, or `"auto"` — the model's own
        window up to the 400k accuracy ceiling, the default), `model` (summarizer pin, "" = the session's own model),
        `instructions` (replaces the built-in summary prompt when non-empty)."""
        from harness.context.compaction import DEFAULT_BUFFER_TOKENS, DEFAULT_KEEP_TOKENS

        table = self._compaction_table(workspace)

        def raw(key: str, pref: str) -> Any:
            return table[key] if key in table else self._prefs.get(pref)

        def as_int(value: Any, default: int) -> int:
            try:
                return max(0, min(int(value), 2_000_000))
            except (TypeError, ValueError):
                return default

        buffer_tokens = raw("buffer_tokens", "compaction_buffer_tokens")
        keep_tokens = raw("keep_tokens", "compaction_keep_tokens")
        caps = raw("max_context_tokens", "compaction_max_context_tokens")
        if caps is None or (
            isinstance(caps, str) and caps.strip().lower() == "auto"
        ):
            # The default: the model's own window, up to the accuracy ceiling.
            max_context: Any = "auto"
        else:
            try:
                max_context = max(0, min(int(caps), 2_000_000))
            except (TypeError, ValueError):
                max_context = "auto"
        return {
            "buffer_tokens": (
                DEFAULT_BUFFER_TOKENS
                if buffer_tokens is None
                else as_int(buffer_tokens, DEFAULT_BUFFER_TOKENS)
            ),
            "keep_tokens": (
                DEFAULT_KEEP_TOKENS
                if keep_tokens is None
                else as_int(keep_tokens, DEFAULT_KEEP_TOKENS)
            ),
            "max_context_tokens": max_context,
            # "" → the session's own model (engine falls back to self.model).
            "model": str(raw("model", "compaction_model") or ""),
            # "" → the built-in 8-section summary prompt. Non-empty REPLACES it.
            "instructions": str(raw("instructions", "compaction_instructions") or "")[
                :8_000
            ],
        }

    def compaction_settings_payload(self) -> dict[str, Any]:
        """The same knobs under REST-facing names (prefixed to keep /v1/settings flat)."""
        settings = self.compaction_settings()
        return {
            "compaction_buffer_tokens": settings["buffer_tokens"],
            "compaction_keep_tokens": settings["keep_tokens"],
            "compaction_max_context_tokens": settings["max_context_tokens"],
            "compaction_model": settings["model"],
            "compaction_instructions": settings["instructions"],
        }

    def set_compaction_settings(
        self,
        buffer_tokens: Any = None,
        keep_tokens: Any = None,
        model: Any = None,
        max_context_tokens: Any = None,
        instructions: Any = None,
    ) -> dict[str, Any]:
        """Persist the auto-compaction overrides — the LEGACY pref path, kept for
        tests and migration: compaction is configured in config.toml `[compaction]`, and a
        key the file names beats anything written here. `buffer_tokens` is the safety
        margin kept for the next answer; `keep_tokens` is how
        much recent history stays verbatim; `max_context_tokens` caps the working window
        whatever the model declares (a number, or "auto"); `model` pins the summarizer
        ('' → the session's own model); `instructions` replaces the summary prompt."""
        if buffer_tokens is not None:
            try:
                self._prefs["compaction_buffer_tokens"] = max(
                    0, min(int(buffer_tokens), 2_000_000)
                )
            except (TypeError, ValueError):
                return {"ok": False, "error": "compaction_buffer_tokens must be a number"}
        if keep_tokens is not None:
            try:
                self._prefs["compaction_keep_tokens"] = max(
                    0, min(int(keep_tokens), 2_000_000)
                )
            except (TypeError, ValueError):
                return {"ok": False, "error": "compaction_keep_tokens must be a number"}
        if max_context_tokens is not None:
            if (
                isinstance(max_context_tokens, str)
                and max_context_tokens.strip().lower() == "auto"
            ):
                self._prefs["compaction_max_context_tokens"] = "auto"
            else:
                try:
                    self._prefs["compaction_max_context_tokens"] = max(
                        0, min(int(max_context_tokens), 2_000_000)
                    )
                except (TypeError, ValueError):
                    return {
                        "ok": False,
                        "error": "compaction_max_context_tokens must be a number or 'auto'",
                    }
        if model is not None:
            self._prefs["compaction_model"] = str(model)
        if instructions is not None:
            # Bounded: this is a prompt, not a document store. 8k chars is far past the
            # default prompt and still cheap to carry in a one-off summarizer call.
            self._prefs["compaction_instructions"] = str(instructions)[:8_000]
        self._save_prefs()
        return {"ok": True, **self.compaction_settings()}

    def set_pdf_settings(
        self,
        fallback: Any = None,
        max_pages: Any = None,
        max_mb: Any = None,
    ) -> dict[str, Any]:
        from harness.llm.pdf_support import FALLBACK_MODES, set_fallback_mode

        if fallback is not None:
            if fallback not in FALLBACK_MODES:
                return {"ok": False, "error": "pdf_fallback must be 'text' or 'images'"}
            self._prefs["pdf_fallback"] = fallback
        for key, value, ceiling in (
            ("pdf_max_pages", max_pages, 100),
            ("pdf_max_mb", max_mb, 10),
        ):
            if value is None:
                continue
            try:
                self._prefs[key] = max(1, min(int(value), ceiling))
            except (TypeError, ValueError):
                return {"ok": False, "error": f"{key} must be a number"}
        self._save_prefs()
        settings = self.pdf_settings()
        set_fallback_mode(settings["pdf_fallback"])  # engines read the module global
        return {"ok": True, **settings}

    def set_model_key(self, api_key: str) -> dict[str, Any]:
        """Persist the model API key to the SecretStore (0600). The new provider client is
        built lazily on the next turn, so it picks the key up without a restart."""
        api_key = (api_key or "").strip()
        if not api_key:
            return {"ok": False, "error": "empty api key"}
        # Merge, don't replace: the profile may also hold a custom endpoint (base_url).
        profile = dict(self.secrets.get("provider:openai") or {})
        profile.update({"type": "api_key", "api_key": api_key})
        self.secrets.put("provider:openai", profile)
        self._refresh_provider("openai")  # rebuild the OpenAI client with the new key
        return {"ok": True, **self.get_settings()}

    def set_default_model(self, model: str) -> dict[str, Any]:
        """Set + persist the default model for new sessions (the UI pre-selects it)."""
        model = (model or "").strip()
        if not model:
            return {"ok": False, "error": "empty model"}
        self.model = model
        self._prefs["default_model"] = model
        self._save_prefs()
        return {"ok": True, **self.get_settings()}

    def set_default_mode(self, mode: str) -> dict[str, Any]:
        """Set + persist the permission mode new conversations start in. Only new ones: a
        conversation keeps the mode it was left in, and its own picker changes only it."""
        from harness.safety.permissions import Mode

        try:
            chosen = Mode(str(mode or "").strip())
        except ValueError:
            return {"ok": False, "error": f"unknown mode: {mode!r}"}
        self.mode = chosen
        self._prefs["default_mode"] = chosen.value
        self._save_prefs()
        return {"ok": True, **self.get_settings()}

    def set_session_thinking(self, session_id: str, level: Optional[str]) -> Optional[str]:
        """Set a session's thinking level and persist it. Returns the notice text, or None
        when nothing changed (or there is no history to mark it in).

        Works with or without a live engine: a session that is open gets the patch applied
        to its live `model_settings`; a session with no engine records the level so the
        next `get_engine` builds with it. `""`/"default" clears the override.
        """
        level = (level or "").strip() or None
        if level == "default":
            level = None
        engine = self._engines.get(session_id)
        if engine is not None:
            before = getattr(engine, "thinking_level", None)
            notice = engine.switch_thinking(level)
            if getattr(engine, "thinking_level", None) != before:
                self.save(session_id, engine)
            return notice
        record = self.session_store.load(session_id)
        if record is not None and (record.thinking or None) != level:
            record.thinking = level
            self.session_store.save(record)
        return None

    def set_onboarded(self, value: bool = True) -> dict[str, Any]:
        """Record that first-run setup is complete (so it isn't shown again)."""
        self._prefs["onboarded"] = bool(value)
        self._save_prefs()
        return {"ok": True, "onboarded": bool(value)}

    def set_scratch_base(self, path: str) -> dict[str, Any]:
        """Set + persist the common area where each conversation's scratch directory is
        created (default ~/Reeve). The raw value is stored so the UI shows it as entered;
        new conversations use it immediately (existing ones keep their provisioned dir).
        """
        path = (path or "").strip()
        if not path:
            return {"ok": False, "error": "empty path"}
        try:
            Path(path).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        self._prefs["scratch_base"] = path
        self._save_prefs()
        return {"ok": True, **self.get_settings()}
