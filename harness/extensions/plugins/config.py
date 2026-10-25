"""A plugin's `userConfig`: the values a person gives it (design §4.5).

Each option declares `type` (string | number | boolean | directory | file), `title` and
`description`, and may add `required`, `default`, `options`, `multiple`, `sensitive`,
`min`, `max`. An unknown key is an error for the plugin — the object is strict, as in
Claude Code.

A non-sensitive value is kept in `plugins/config.json` under the plugin's id. A sensitive
one goes to the SecretStore (profile `plugin:<id>`) and nowhere else: never a JSON file,
a log line, an error message, or a prompt. `${user_config.KEY}` in a skill, agent or
command body becomes the value — or `‹sensitive:KEY›` for a sensitive one, because a body
is text the model reads.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from . import store

TYPES = ("string", "number", "boolean", "directory", "file")
_OPTION_KEYS = {
    "type", "title", "description", "required", "default", "options", "multiple", "sensitive", "min", "max",
}
_REF = re.compile(r"\$\{user_config\.([A-Za-z0-9_]+)\}")


def options_of(raw: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The manifest's userConfig options, and why any of it is invalid."""
    value = raw.get("userConfig")
    if value is None:
        return {}, []
    if not isinstance(value, dict):
        return {}, ["userConfig must be an object"]
    errors: list[str] = []
    out: dict[str, dict[str, Any]] = {}
    for key, option in value.items():
        if not re.fullmatch(r"[A-Za-z0-9_]+", str(key)):
            errors.append(f"userConfig key {key!r} may use letters, digits and _ only")
            continue
        if not isinstance(option, dict):
            errors.append(f"userConfig.{key} must be an object")
            continue
        unknown = sorted(set(option) - _OPTION_KEYS)
        if unknown:
            errors.append(f"userConfig.{key}: unknown keys {', '.join(unknown)}")
        missing = [k for k in ("type", "title", "description") if not option.get(k)]
        if missing:
            errors.append(f"userConfig.{key}: needs {', '.join(missing)}")
        if option.get("type") not in TYPES:
            errors.append(f"userConfig.{key}: type must be one of {', '.join(TYPES)}")
        out[str(key)] = option
    return out, errors


def _coerce(key: str, option: dict[str, Any], value: Any) -> tuple[Any, Optional[str]]:
    kind = option.get("type")
    if kind == "boolean":
        if isinstance(value, bool):
            return value, None
        return None, f"{key} must be true or false"
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, f"{key} must be a number"
        if "min" in option and value < option["min"]:
            return None, f"{key} must be at least {option['min']}"
        if "max" in option and value > option["max"]:
            return None, f"{key} must be at most {option['max']}"
        return value, None
    items = value if option.get("multiple") and isinstance(value, list) else [value]
    if not all(isinstance(v, str) for v in items):
        return None, f"{key} must be text"
    allowed = option.get("options")
    if isinstance(allowed, list) and any(v not in allowed for v in items):
        return None, f"{key} must be one of {', '.join(map(str, allowed))}"
    return value, None


def validate(options: dict[str, dict[str, Any]], values: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """`(clean values, problems)`. A problem names the key, never a value."""
    clean: dict[str, Any] = {}
    problems: list[str] = []
    for key, value in (values or {}).items():
        option = options.get(key)
        if option is None:
            problems.append(f"{key} is not one of this plugin's settings")
            continue
        coerced, problem = _coerce(key, option, value)
        if problem:
            problems.append(problem)
        else:
            clean[key] = coerced
    return clean, problems


def save(plugin_id: str, options: dict[str, dict[str, Any]], values: dict[str, Any]) -> None:
    """Keep validated values: plain ones in config.json, sensitive ones in the SecretStore."""
    from harness.safety.secrets import SecretStore

    plain = {k: v for k, v in values.items() if not options.get(k, {}).get("sensitive")}
    secret = {k: v for k, v in values.items() if options.get(k, {}).get("sensitive")}
    path = store.plugins_dir() / "config.json"
    data = store.read_json(path, {})
    data[plugin_id] = {**(data.get(plugin_id) or {}), **plain}
    store.write_json(path, data)
    if secret:
        secrets = SecretStore()
        profile = f"plugin:{plugin_id}"
        secrets.put(profile, {**(secrets.get(profile) or {}), **secret})


def values(plugin_id: str, options: dict[str, dict[str, Any]], *, include_sensitive: bool = False) -> dict[str, Any]:
    """The plugin's values (defaults filled). Sensitive ones only when asked for — a
    process environment, never a body the model reads."""
    stored = store.read_json(store.plugins_dir() / "config.json", {}).get(plugin_id) or {}
    out = {k: o["default"] for k, o in options.items() if "default" in o and not o.get("sensitive")}
    out.update({k: v for k, v in stored.items() if k in options and not options[k].get("sensitive")})
    if include_sensitive:
        from harness.safety.secrets import SecretStore

        out.update(SecretStore().get(f"plugin:{plugin_id}") or {})
    return out


def missing(plugin_id: str, options: dict[str, dict[str, Any]]) -> list[str]:
    """Required settings with no value yet (keys only)."""
    have = values(plugin_id, options)
    secret_keys: set[str] = set()
    if any(o.get("sensitive") for o in options.values()):
        from harness.safety.secrets import SecretStore

        secret_keys = set(SecretStore().get(f"plugin:{plugin_id}") or {})
    return [
        k for k, o in options.items() if o.get("required") and k not in have and k not in secret_keys
    ]


def forget(plugin_id: str) -> None:
    """Remove every value, plain and sensitive (an uninstall, design §8.7)."""
    from harness.safety.secrets import SecretStore

    path = store.plugins_dir() / "config.json"
    data = store.read_json(path, {})
    if data.pop(plugin_id, None) is not None:
        store.write_json(path, data)
    SecretStore().delete(f"plugin:{plugin_id}")


def substitute(text: str, plugin_id: str, options: dict[str, dict[str, Any]]) -> str:
    """`${user_config.KEY}` in a body the model reads: the value, or a placeholder for a
    sensitive one (and for a key with no value, left as written)."""
    if not text or "${user_config." not in text:
        return text
    have = values(plugin_id, options)

    def value(match: re.Match[str]) -> str:
        key = match.group(1)
        if options.get(key, {}).get("sensitive"):
            return f"‹sensitive:{key}›"
        if key in have:
            item = have[key]
            return ", ".join(map(str, item)) if isinstance(item, list) else str(item)
        return match.group(0)

    return _REF.sub(value, text)
