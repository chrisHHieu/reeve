"""Reading one TOML file: where the global one lives, and `{file:...}` / `{env:...}` references."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

try:
    import tomllib  # stdlib since 3.11
except ModuleNotFoundError:  # 3.10, the floor requires-python declares
    import tomli as tomllib  # type: ignore[no-redef]

from harness.safety.secrets import state_dir

logger = logging.getLogger("harness.config.config")

#: `{file:path}` / `{env:VAR}` in a config STRING value. The substitution runs after the TOML parse,
#: which is equivalent for every string a person can write. `{file:...}` resolves relative to the
#: config file that declared the value.
_REFERENCE = re.compile(r"\{file:([^}]+)\}|\{env:([^}]+)\}")

#: One warning per distinct reference per process, so a missing variable in a hot config path does
#: not become a log flood.
_reported: set[str] = set()


def global_config_path() -> Path:
    return state_dir() / "config.toml"


def _report_once(key: str, message: str) -> None:
    if key not in _reported:
        _reported.add(key)
        logger.warning(message)


def _resolve(match: "re.Match[str]", base: Path) -> str:
    wanted_file, wanted_env = match.group(1), match.group(2)
    if wanted_file is not None:
        path = Path(wanted_file).expanduser()
        path = path if path.is_absolute() else base / path
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            _report_once(
                f"file:{path}",
                f"config: {match.group(0)} did not resolve ({path}) — leaving the reference as written",
            )
            return match.group(0)
    value = os.environ.get(wanted_env or "")
    if value is None:
        _report_once(
            f"env:{wanted_env}",
            f"config: {match.group(0)} is not set — leaving the reference as written",
        )
        return match.group(0)
    return value


def _substitute_text(text: str, base: Path) -> str:
    """One string with its references resolved. A reference that does not resolve stays literal and
    is reported once, because a typo in a config file must not cost the person every session."""
    if "{" not in text:
        return text
    return _REFERENCE.sub(lambda match: _resolve(match, base), text)


def _substitute_values(value: Any, base: Path) -> Any:
    """A parsed document with every string leaf substituted. Keys are left alone: a key is never a
    path or a secret."""
    if isinstance(value, str):
        return _substitute_text(value, base)
    if isinstance(value, dict):
        return {key: _substitute_values(item, base) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute_values(item, base) for item in value]
    return value


def read_toml(path: Path) -> dict[str, Any]:
    """The file's table with references resolved; `{}` when it is missing or does not parse."""
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return _substitute_values(data, path.parent)


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """`overlay` over `base` per leaf, siblings surviving: a markdown file that writes only
    `description` keeps the TOML `model`."""
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
