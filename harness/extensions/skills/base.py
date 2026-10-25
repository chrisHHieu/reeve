"""Reading skills: SKILL.md files, the menu the model is offered, and the `load_skill` tool.

A skill is a folder whose `SKILL.md` starts with YAML frontmatter (`name`, `description`, and any
other keys, which are kept as they are) and goes on with the instructions in markdown; the rest of
the folder is its resources. A lone `<name>.md` next to the folders is a skill as well, with the
folder it sits in as its resources.

The model is first shown only a menu, one line a skill, and asks for a body with `load_skill` when
a task needs it: a hundred installed skills cost a hundred lines, not a hundred documents.

How a file is read:

* A byte-order mark is dropped and line endings made `\\n` before anything else; the frontmatter is
  then the block between a first line `---` and the next line that is exactly `---` (so `----`
  inside a quoted value or a block scalar ends nothing), read with `yaml.safe_load`.
* With no description a skill stays loadable by name and is left off the menu.
* A block YAML refuses, such as `description: says: hello` from the writer this app once had, is
  read line by line instead, so those files keep working.
* Each file is read on its own, and one that cannot be read is logged and passed over.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Union

import aisuite as ai
import yaml

from harness.tools import cards
from harness.tools.contract import ToolContract, ToolPresentation, attach_contract

logger = logging.getLogger(__name__)

#: A skill body shown in the transcript is bounded: the model still gets the whole thing
#: from the tool result, but the card that the GUI embeds (and the session log stores) does
#: not.
_SKILL_CARD_CHARS = 12000

#: Catalog descriptions are normalized and capped (default 500, minimum 3). The cap INCLUDES
#: the trailing ellipsis.
_CATALOG_DESCRIPTION_MAX_CHARS = 500

#: The whole rendered catalog's character budget. Past it the entries degrade in two
#: steps — descriptions shortened, then names only — and the text SAYS which step it took.
_CATALOG_TOTAL_MAX_CHARS = 20_000

#: One entry's cap at the first degradation step.
_CATALOG_TIGHT_DESCRIPTION_CHARS = 160

#: Public skill-name grammar. Load-time only:
#: a name that fails it is warned about, never dropped — legacy CRUD names keep working.
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: The agent-skills spec caps `compatibility` at 500 chars.
#: Overlong values are warned about and clamped; a strict
#: parser would reject them, but the load posture is warn-never-drop.
_COMPATIBILITY_MAX_CHARS = 500

#: Legacy camelCase invocation keys and their canonical replacements. We warn and ignore
#: them, then apply the safe default, so a legacy file stays loadable instead of vanishing from the catalog.
_LEGACY_INVOCATION_KEYS: dict[str, str] = {
    "disableModelInvocation": "disable-model-invocation",
    "modelInvocable": "disable-model-invocation",
    "userInvocable": "user-invocable",
}

_TRUTHY_STRINGS = frozenset({"true", "yes", "on", "1"})
_FALSY_STRINGS = frozenset({"false", "no", "off", "0"})


@dataclass
class Skill:
    """One skill as read from its file."""

    name: str
    description: str
    instructions: str = ""  # the body, sent only when `load_skill` asks for it
    path: Optional[str] = None  # the folder its resources are in
    # Frontmatter fields Settings and the menu show. They are read here and kept in `extra` as
    # well, so writing the file back changes none of them.
    when_to_use: str = ""
    # A short line written for people (`summary:`): the composer's "/" menu shows it in place
    # of `description`, which is written for the model ("Use this skill whenever…"). Never
    # model-facing.
    summary: str = ""
    license: str = ""
    compatibility: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    # Invocation policy: the model may offer/load this
    # skill; the user may force-run it from the GUI's `/` menu.
    model_invocable: bool = True
    user_invocable: bool = True
    # `context: fork`: run the skill in a subagent so its tool calls and
    # results never enter this conversation — only the result comes back. "main" (the
    # default) is the ordinary inline load.
    context: str = "main"
    # Frontmatter keys we do not own (license, metadata, source, a legacy allowed-tools…),
    # kept verbatim so a read → rewrite round-trip never silently drops them.
    extra: dict[str, Any] = field(default_factory=dict)
    # The plugin this skill came from (`<name>@<origin>`), or "" for a person's own.
    plugin: str = ""


class SkillLoader:
    """The skills under a list of roots, read nearest last: a later root's copy of a name wins."""

    def __init__(self, dirs: list[Any]) -> None:
        # A root is a folder, or a plugin's skill root (`plugins.PluginSkillRoot`, which has a
        # `plugin`) placed in the list at the rank its precedence gives it.
        self._dirs = [root if hasattr(root, "plugin") else Path(root) for root in dirs]
        self._skills: dict[str, Skill] = {}
        self.rescan()

    def rescan(self) -> None:
        """Read every root again. `load_skill` does this when a name is not found, so a skill
        made after the engine was built can still be loaded by name."""
        self._skills = {}
        for root in self._dirs:
            (self._discover_plugin if hasattr(root, "plugin") else self._discover)(root)

    def _discover_plugin(self, root: Any) -> None:
        """A plugin's skills, named `<plugin>:<name>` so two plugins — or a plugin and the
        person — never collide. `${CLAUDE_PLUGIN_ROOT}` and its aliases in the
        body name the plugin's own folder."""
        folders = (
            [root.path]
            if root.single
            else [p for p in sorted(Path(root.path).iterdir()) if p.is_dir()]
            if Path(root.path).is_dir()
            else []
        )
        for folder in folders:
            md = folder / "SKILL.md"
            if not md.is_file():
                continue
            try:
                skill = _parse_skill(md)
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                logger.warning("plugin skill ignored: %s: %s", md, exc)
                continue
            skill.name = f"{root.plugin}:{skill.name}"
            skill.plugin = getattr(root, "plugin_id", "") or f"{root.plugin}@inline"
            plugin_root = getattr(root, "plugin_root", None)
            if plugin_root is not None and skill.instructions:
                from ..plugins import substitute_body

                skill.instructions = substitute_body(
                    skill.instructions,
                    plugin_root=Path(plugin_root),
                    plugin_id=getattr(root, "plugin_id", "") or f"{root.plugin}@inline",
                )
            self._skills[skill.name] = skill

    @staticmethod
    def _skill_file(entry: Path) -> tuple[Optional[Path], bool]:
        """The skill file `entry` stands for, and whether it is a lone `.md` (which then must
        name itself, having no folder to be named by). One level only: a `SKILL.md` deeper down
        is a resource of some other skill, not a skill."""
        if entry.is_dir():
            inner = entry / "SKILL.md"
            return (inner if inner.is_file() else None), False
        if entry.is_file() and entry.suffix.lower() == ".md":
            return entry, True
        return None, False

    def _discover(self, directory: Path) -> None:
        if not directory.is_dir():
            return
        try:
            entries = sorted(directory.iterdir())
        except OSError as exc:
            logger.warning("skills directory ignored: %s: %s", directory, exc)
            return
        for entry in entries:
            try:
                md, lone = self._skill_file(entry)
            except OSError as exc:
                logger.warning("skill ignored: %s: %s", entry, exc)
                continue
            if md is None:
                continue
            try:
                skill = _parse_skill(md, require_name=lone)
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                logger.warning("skill ignored: %s: %s", md, exc)  # the others still load
                continue
            _warn_name_policy(skill, md, flat=lone)
            self._skills[skill.name] = skill

    def names(self) -> list[str]:
        return list(self._skills)

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def catalog(self) -> list[dict]:
        """Every skill's menu fields. Which of them reach the model is `skill_catalog_text`'s
        choice; the side panel and the manager read this same list, so no file is read twice."""
        rows = []
        for skill in self._skills.values():
            row = {
                field_name: getattr(skill, field_name)
                for field_name in ("name", "description", "when_to_use", "model_invocable", "user_invocable", "context")
            }
            if skill.plugin:
                row["plugin"] = skill.plugin
            rows.append(row)
        return rows


def _warn_name_policy(skill: Skill, md: Path, *, flat: bool) -> None:
    """Load warns, never drops: an invalid name is warned about and the skill kept, as is a
    folder mismatch, so legacy CRUD-authored skills stay usable."""
    if not _SKILL_NAME_RE.match(skill.name):
        logger.warning(
            "skill %s: name %r is not lowercase-kebab-case (kept for compatibility)",
            md,
            skill.name,
        )
    reference = md.stem if flat else md.parent.name
    if skill.name != reference:
        logger.warning(
            "skill %s: name %r does not match its %s %r (kept)",
            md,
            skill.name,
            "file" if flat else "folder",
            reference,
        )


def _normalize_text(text: str) -> str:
    """Strip a leading UTF-8 BOM and normalize newlines before frontmatter detection.

    The BOM matters because a file saved as "UTF-8 with BOM" (Windows Notepad) does not
    start with ``---`` byte-wise. CRLF is normalized so the
    delimiter scan is uniform across platforms.
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _find_closing_frontmatter(text: str, start: int) -> Optional[tuple[int, int]]:
    """(start of the closing delimiter line, index just past its newline).

    Only a line that is exactly ``---`` closes the block:
    a ``----`` marker inside a quoted value or a
    block scalar is data, not framing.
    """
    line_start = start
    while line_start <= len(text):
        newline = text.find("\n", line_start)
        line_end = len(text) if newline < 0 else newline
        if text[line_start:line_end] == "---":
            return line_start, (len(text) if newline < 0 else newline + 1)
        if newline < 0:
            return None
        line_start = newline + 1
    return None


def _yaml_mapping(raw: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML frontmatter: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("frontmatter must be a YAML mapping")
    return data


def _legacy_frontmatter(raw: str) -> dict[str, Any]:
    """The old line-split reader (pre-PyYAML ``_parse_skill``): one ``key: value`` per line,
    first colon splits. Only reached when safe_load rejects a block — the old writer
    emitted raw values, so a description containing ``: `` is the common case."""
    data: dict[str, Any] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        if key and key not in data:
            data[key] = value.strip()
    return data


def _split_frontmatter(text: str, path: Optional[str] = None) -> tuple[dict[str, Any], str]:
    """(frontmatter mapping, body). Never raises for content shape: an opened-but-unclosed
    block is treated as no frontmatter (the legacy reader's posture — the whole file is
    the body) and a YAML error falls back to the legacy line reader."""
    if not text.startswith("---"):
        return {}, text
    first_newline = text.find("\n")
    if first_newline == -1 or text[:first_newline] != "---":
        return {}, text
    closing = _find_closing_frontmatter(text, first_newline + 1)
    if closing is None:
        logger.warning(
            "skill %s: unterminated frontmatter — treating the whole file as instructions",
            path,
        )
        return {}, text
    block_start, body_start = closing
    raw = text[first_newline + 1 : block_start]
    try:
        return _yaml_mapping(raw), text[body_start:]
    except ValueError as exc:
        logger.warning("skill %s: %s — read with the legacy line reader", path, exc)
        return _legacy_frontmatter(raw), text[body_start:]


def _lookup_field(data: dict[str, Any], key: str) -> Any:
    if key in data:
        return data[key]
    lowered = key.lower()
    for name, value in data.items():
        if isinstance(name, str) and name.lower() == lowered:
            return value
    return None


def _string_field(data: dict[str, Any], key: str) -> str:
    value = _lookup_field(data, key)
    if value is None:
        return ""
    return str(value).strip()


def _has_field(data: dict[str, Any], key: str) -> bool:
    """Case-insensitive key presence — distinguishes an absent key from an explicit null."""
    if key in data:
        return True
    lowered = key.lower()
    return any(isinstance(name, str) and name.lower() == lowered for name in data)


def _boolean_field(
    data: dict[str, Any], key: str, *, default: bool, path: Optional[str]
) -> bool:
    """Parse one invocation flag with the documented boolean spellings.

    ``true/false``, ``yes/no``, ``on/off``, ``1/0`` — as YAML booleans/ints or strings,
    case-insensitive. Anything else is warned about and
    falls back to the safe default rather than hiding the whole skill (the load posture is
    warn-never-drop).
    """
    if not _has_field(data, key):
        return default
    value = _lookup_field(data, key)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUTHY_STRINGS:
            return True
        if lowered in _FALSY_STRINGS:
            return False
    logger.warning(
        "skill %s: frontmatter field %r must be a boolean (got %r) — using %s",
        path,
        key,
        value,
        default,
    )
    return default


def _metadata_field(data: dict[str, Any], path: Optional[str]) -> dict[str, str]:
    """The string→string ``metadata`` map.

    Non-string values are warned about and skipped; the raw mapping still round-trips through
    ``extra`` so nothing is lost on a rewrite.
    """
    value = _lookup_field(data, "metadata")
    if value is None:
        return {}
    if not isinstance(value, dict):
        logger.warning("skill %s: frontmatter field 'metadata' must be a mapping", path)
        return {}
    out: dict[str, str] = {}
    for key, entry in value.items():
        if isinstance(entry, str):
            out[str(key)] = entry
        else:
            logger.warning(
                "skill %s: metadata.%s must be a string (got %r) — ignored",
                path,
                key,
                entry,
            )
    return out


def _invocation_policy(
    data: dict[str, Any], path: Optional[str]
) -> tuple[bool, bool]:
    """(model_invocable, user_invocable) from the kebab-case flags.

    Legacy camelCase keys are rejected with an explicit warning naming the replacement;
    invalid values warn and fall back to the safe default — model invocation enabled, user
    invocation enabled.
    """
    for legacy, canonical in _LEGACY_INVOCATION_KEYS.items():
        if _has_field(data, legacy):
            logger.warning(
                "skill %s: frontmatter field %r is unsupported — use %r",
                path,
                legacy,
                canonical,
            )
    disable_model = _boolean_field(
        data, "disable-model-invocation", default=False, path=path
    )
    user_invocable = _boolean_field(data, "user-invocable", default=True, path=path)
    return (not disable_model, user_invocable)


def _context_field(data: dict[str, Any]) -> str:
    """`context: fork` runs the skill in a subagent; the default is the main conversation.
    Unknown values fall back to main — a typo must not silently fork."""
    raw = str(data.get("context") or "").strip().lower()
    return "fork" if raw in ("fork", "subagent") else "main"


def _compatibility_field(data: dict[str, Any], path: Optional[str]) -> str:
    value = _string_field(data, "compatibility")
    if len(value) > _COMPATIBILITY_MAX_CHARS:
        logger.warning(
            "skill %s: compatibility exceeds %d characters — truncated",
            path,
            _COMPATIBILITY_MAX_CHARS,
        )
        return value[:_COMPATIBILITY_MAX_CHARS]
    return value


def _read_skill(md: Path, *, require_name: bool = False) -> tuple[Skill, dict[str, Any]]:
    """(parsed skill, raw frontmatter mapping) from one file read.

    ``require_name`` is for flat files: with no folder to fall back to, a missing name is
    an error (a nameless flat `.md` is skipped).
    """
    text = _normalize_text(md.read_text(encoding="utf-8"))
    data, body = _split_frontmatter(text, path=str(md))
    name = _string_field(data, "name")
    if not name:
        if require_name:
            raise ValueError("missing 'name' in frontmatter")
        name = md.parent.name
    extra = {
        key: value
        for key, value in data.items()
        if not (isinstance(key, str) and key.lower() in ("name", "description"))
    }
    model_invocable, user_invocable = _invocation_policy(data, str(md))
    skill = Skill(
        name,
        _string_field(data, "description"),
        body.strip(),
        str(md.parent),
        when_to_use=_string_field(data, "whenToUse"),
        summary=_string_field(data, "summary"),
        license=_string_field(data, "license"),
        compatibility=_compatibility_field(data, str(md)),
        metadata=_metadata_field(data, str(md)),
        model_invocable=model_invocable,
        user_invocable=user_invocable,
        context=_context_field(data),
        extra=extra,
    )
    return skill, data


def _parse_skill(md: Path, *, require_name: bool = False) -> Skill:
    return _read_skill(md, require_name=require_name)[0]


def _catalog_description(value: str) -> str:
    """Whitespace-normalized and capped: the cap includes the trailing ``...``."""
    normalized = re.sub(r"\s+", " ", value or "").strip()
    if len(normalized) <= _CATALOG_DESCRIPTION_MAX_CHARS:
        return normalized
    return normalized[: _CATALOG_DESCRIPTION_MAX_CHARS - 3] + "..."


def _escape_text(value: str) -> str:
    """Escape model-facing prose inside pseudo-XML framing."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _catalog_entry(entry: dict, *, description_cap: Optional[int] = None) -> str:
    """One ``- `name`: description`` line, followed by ``Use when: …`` when the skill
    declares `whenToUse` (rendered as its own catalog field; the whole rendered entry
    stays inside the 500-char cap that a description alone is capped at).

    `description_cap` is the DEGRADED step's tighter budget: when the caller passes one,
    it bounds the whole line (name included), because the point is fitting many skills in
    a bounded catalog. The default path keeps the original rule — the 500-char ceiling
    applies to the description, and to the whole entry once a `Use when:` clause joins it.
    """
    cap = description_cap or _CATALOG_DESCRIPTION_MAX_CHARS
    line = (
        f"- `{_escape_text(entry['name'])}`: "
        f"{_escape_text(_catalog_description(entry['description']))}"
    )
    when = _catalog_description(entry.get("when_to_use") or "")
    if when:
        line += f" — Use when: {_escape_text(when)}"
    # The appended guidance must fit the same budget as a description alone: cap the
    # WHOLE rendered entry (prefix included) once a `Use when:` clause is present — or
    # whenever the caller asked for a tighter budget than the per-description ceiling.
    if (when or description_cap is not None) and len(line) > cap:
        line = line[: cap - 3] + "..."
    return line + _source(entry)


def _source(entry: dict) -> str:
    """Where a plugin's skill comes from, after any cut so it is never cut: the model and a
    reader of the log see which text a plugin supplied (design §9.7)."""
    return f" (plugin: {_escape_text(entry['plugin'])})" if entry.get("plugin") else ""


def _catalog_names_only(entry: dict) -> str:
    """The degraded line: the name and nothing else. A catalog too large to describe is
    still a catalog the model can call `load_skill` on."""
    return f"- `{_escape_text(entry['name'])}`" + _source(entry)


def _on_menu(entry: dict, allowed: Optional[set[str]]) -> bool:
    """A skill the model is offered: it has a description (one without is loadable by name and
    never listed), it is not `disable-model-invocation` (a person runs it from "/", the model
    never sees it) and, when there is a filter, the filter lets it through."""
    return bool((entry.get("description") or "").strip()) and entry.get("model_invocable", True) and (allowed is None or entry["name"] in allowed)


def skill_catalog_text(loader: SkillLoader, allowed: Optional[set[str]] = None) -> str:
    catalog = [entry for entry in loader.catalog() if _on_menu(entry, allowed)]
    if not catalog:
        return ""
    header = (
        "Available skills — call load_skill(name) to load one's full instructions when "
        "it's relevant to the task:\n<available_skills>\n"
    )
    footer = "\n</available_skills>"
    lines = [_catalog_entry(c) for c in catalog]
    # A large install must not silently eat the context window (the catalog is capped at
    # 20k characters and degrades to names): past the budget the descriptions are
    # shortened, and if even that does not fit, only the names remain. Both degradations
    # are STATED in the text — a model reading a bare list deserves to know why, and a
    # reader of a session log should not have to diff catalogs to discover the cut.
    if len(header) + len("\n".join(lines)) + len(footer) > _CATALOG_TOTAL_MAX_CHARS:
        lines = [
            _catalog_entry(c, description_cap=_CATALOG_TIGHT_DESCRIPTION_CHARS)
            for c in catalog
        ]
        note = (
            f"\n({len(catalog)} skills; descriptions shortened to fit the catalog "
            "budget — load_skill(name) for any one in full.)"
        )
        if (
            len(header) + len("\n".join(lines)) + len(note) + len(footer)
            > _CATALOG_TOTAL_MAX_CHARS
        ):
            lines = [_catalog_names_only(c) for c in catalog]
            note = (
                f"\n({len(catalog)} skills; the catalog is too large for descriptions — "
                "load_skill(name) by name.)"
            )
        return header + "\n".join(lines) + note + footer
    return header + "\n".join(lines) + footer


AllowedSkills = Union[set, Callable[[], set], None]


def _load_skill_render(arguments: dict, value: object) -> str:
    """The skill body verbatim, headed by the skill name and its resources path.

    Without this the body arrives JSON-escaped behind a `{"name","instructions",…}`
    wrapper — and the body is the whole point of the call.
    """
    if not isinstance(value, dict):
        return json.dumps(value, default=str)
    error = str(value.get("error") or "").strip()
    if error:
        available = value.get("available")
        text = f"Error: {error}"
        if isinstance(available, list) and available:
            text += "\nAvailable: " + ", ".join(str(name) for name in available)
        return text
    name = str(value.get("name") or arguments.get("name") or "skill")
    path = str(value.get("resources_path") or "")
    head = f"[skill {name}]" + (f" resources: {path}" if path else "")
    return f"{head}\n{value.get('instructions') or ''}"


def skill_tools(
    loader: SkillLoader,
    allowed: AllowedSkills = None,
    on_use: Optional[Callable[[str], None]] = None,
) -> list:
    """The `load_skill` tool over `loader`. `allowed` filters what it will load: a set is fixed
    when the engine is built; a function is asked on every call, which is what the manager
    passes, so switching a skill off in Settings reaches a conversation already open."""

    def _loadable(gate: Optional[set]) -> list[str]:
        return sorted(name for name in loader.names() if (gate is None or name in gate) and getattr(loader.get(name), "model_invocable", False))

    def load_skill(name: str) -> dict:
        """Get a skill's full instructions and the folder of its resources. Call it when a skill
        on the menu fits the task at hand."""
        skill = loader.get(name)
        if skill is None:
            loader.rescan()  # it may have been made since this conversation began
            skill = loader.get(name)
        gate = allowed() if callable(allowed) else allowed
        if skill is None or (gate is not None and name not in gate):
            return {"error": f"unknown skill: {name}", "available": _loadable(gate)}
        if not skill.model_invocable:
            # Only a person may run a `disable-model-invocation` skill: guessing its name does not
            # get the model in.
            return {"error": f"skill '{name}' is not available for model invocation", "available": _loadable(gate)}
        result: dict[str, Any] = {"name": skill.name, "instructions": skill.instructions, "resources_path": skill.path}
        # A successful load is a use; the hook (the store's usage ledger in the app) must
        # never cost the load itself.
        if on_use is not None:
            try:
                on_use(skill.name)
            except Exception:
                pass
        if skill.context == "fork":
            # `context: fork`. Reeve's fork is the `task` tool — a real
            # child session with its own context, logged and backgroundable — so the
            # instructions say to run there and keep only the result here.
            result["context"] = "fork"
            result["fork"] = (
                "This skill is fork-scoped: run its instructions with the `task` tool "
                "(pass them as the child's prompt, plus the user's arguments) and keep "
                "only the child's result in this conversation."
            )
        return result

    # The card is why a `load_skill` row reads as a skill, not a JSON IN/OUT: without it the
    # surface only ever saw the (capped, escaped) result preview. `present_result` closes over
    # the loader so it can add the description the catalog already showed.
    def present_call(arguments: dict) -> ToolPresentation:
        requested = str(arguments.get("name") or "").strip()
        return ToolPresentation(
            summary=f"Load skill: {requested}" if requested else "Load a skill",
            kind="read",
        )

    def present_result(
        arguments: dict, value: object, card_meta: object = None
    ) -> ToolPresentation:
        if not isinstance(value, dict) or value.get("error"):
            return ToolPresentation(summary="Load a skill", kind="read")
        resolved = str(value.get("name") or arguments.get("name") or "").strip()
        if not resolved:
            return ToolPresentation(summary="Loaded a skill", kind="read")
        loaded = loader.get(resolved)
        body = str(value.get("instructions") or "")
        return ToolPresentation(
            summary=f"Loaded skill: {resolved}",
            kind="read",
            card=cards.skill(
                resolved,
                description=(loaded.description if loaded else "") or None,
                instructions=(body[:_SKILL_CARD_CHARS] or None),
            ),
        )

    attach_contract(
        load_skill,
        ToolContract(
            render=_load_skill_render,
            present_call=present_call,
            present_result=present_result,
        ),
    )

    metadata = ai.ToolMetadata(category="skills", risk_level="low", capabilities=["load_skill"])
    return [ai.tool(load_skill, metadata=metadata)]
