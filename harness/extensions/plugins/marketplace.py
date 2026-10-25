"""Marketplaces: catalogs of plugins and where each comes from (design §7).

A marketplace is a folder or a git repository with `.claude-plugin/marketplace.json`
(`.reeve-plugin/marketplace.json` wins when both exist). A local folder is read where it
is; a remote one is fetched under `state_dir()/plugins/marketplaces/<name>/`. Adding one
never installs anything, and a project may only suggest one (design §7.3).
"""

from __future__ import annotations

import json
import re
import secrets as _secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import store
from .sources import Downloader, SourceError, fetch

MARKETPLACE_PATHS = (
    Path(".reeve-plugin") / "marketplace.json",
    Path(".claude-plugin") / "marketplace.json",
)
#: Marketplace names nobody may add: Reeve's own origins, and names that pose as an
#: official marketplace (design §7.1).
_RESERVED = {"inline", "skills-dir", "synced", "local"}
_RESERVED_PREFIXES = ("claude-", "anthropic-", "reeve-official")
_MARKET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class PluginError(ValueError):
    """A plugin operation that cannot proceed, in words a person can act on."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_marketplace(root: Path) -> dict[str, Any]:
    """A marketplace's `marketplace.json`, validated (design §7.1)."""
    path = next((Path(root) / p for p in MARKETPLACE_PATHS if (Path(root) / p).is_file()), None)
    if path is None:
        raise PluginError("no .claude-plugin/marketplace.json in that marketplace")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PluginError(f"{path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise PluginError("marketplace.json is not an object")
    name = data.get("name")
    owner = data.get("owner")
    if not isinstance(name, str) or not name:
        raise PluginError("marketplace.json needs a `name`")
    if not isinstance(owner, dict) or not owner.get("name"):
        raise PluginError("marketplace.json needs `owner.name`")
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        raise PluginError("marketplace.json needs a `plugins` list")
    entries = []
    for entry in plugins:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str) and "source" in entry:
            entries.append(entry)
    return {**data, "plugins": entries}


def _check_market_name(name: str) -> None:
    lowered = name.lower()
    if lowered in _RESERVED or lowered.startswith(_RESERVED_PREFIXES):
        raise PluginError(f"{name!r} is a reserved marketplace name")
    if not _MARKET_NAME.match(name):
        raise PluginError(f"{name!r} is not a valid marketplace name (ASCII, no spaces)")


def _market_source(value: str) -> Any:
    """What a person typed — `owner/repo`, a git URL, or a folder — as a source."""
    value = value.strip()
    path = Path(value).expanduser()
    if path.is_dir():
        return str(path.resolve())
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        return {"source": "github", "repo": value}
    if value.startswith(("https://", "git@", "ssh://", "file://")):
        return {"source": "url", "url": value}
    raise PluginError(f"{value!r} is not a folder, owner/repo, or a git URL")


def add_marketplace(value: str, *, download: Optional[Downloader] = None) -> dict[str, Any]:
    """Fetch a marketplace and remember it. A local folder is read in place."""
    source = _market_source(value)
    root = store.plugins_dir() / "marketplaces"
    root.mkdir(parents=True, exist_ok=True)
    if isinstance(source, str):
        location, sha = Path(source), ""
    else:
        work = root / f".adding-{_secrets.token_hex(4)}"
        try:
            fetched = fetch(source, work, download=download)
        except SourceError as exc:
            shutil.rmtree(work, ignore_errors=True)
            raise PluginError(str(exc)) from exc
        location, sha = work, fetched.sha
    try:
        data = read_marketplace(location)
        name = str(data["name"])
        _check_market_name(name)
    except PluginError:
        if not isinstance(source, str):
            shutil.rmtree(location, ignore_errors=True)
        raise
    known = store.marketplaces()
    if not isinstance(source, str):
        final = root / name
        shutil.rmtree(final, ignore_errors=True)
        location.rename(final)
        location = final
    known[name] = {
        "source": source,
        "installLocation": str(location),
        "lastUpdated": _now(),
        "autoUpdate": bool((known.get(name) or {}).get("autoUpdate", False)),
        **({"sha": sha} if sha else {}),
    }
    store.save_marketplaces(known)
    return {"name": name, "plugins": len(data["plugins"]), "location": str(location)}


def refresh_marketplace(name: str, *, download: Optional[Downloader] = None) -> dict[str, Any]:
    record = store.marketplaces().get(name)
    if record is None:
        raise PluginError(f"no marketplace named {name}")
    source = record.get("source")
    if isinstance(source, str):
        from .updates import flag_delisted

        count = len(read_marketplace(Path(source))["plugins"])
        return {"name": name, "plugins": count, "flagged": flag_delisted(name)}
    root = store.plugins_dir() / "marketplaces"
    work = root / f".refresh-{_secrets.token_hex(4)}"
    try:
        fetched = fetch(source, work, download=download)
        data = read_marketplace(work)
    except (SourceError, PluginError) as exc:
        shutil.rmtree(work, ignore_errors=True)
        raise PluginError(str(exc)) from exc
    final = Path(record["installLocation"])
    shutil.rmtree(final, ignore_errors=True)
    work.rename(final)
    known = store.marketplaces()
    known[name] = {**record, "lastUpdated": _now(), **({"sha": fetched.sha} if fetched.sha else {})}
    store.save_marketplaces(known)
    from .updates import flag_delisted

    return {"name": name, "plugins": len(data["plugins"]), "flagged": flag_delisted(name)}


def remove_marketplace(name: str) -> None:
    known = store.marketplaces()
    record = known.pop(name, None)
    if record is None:
        raise PluginError(f"no marketplace named {name}")
    in_use = [pid for pid in store.installed() if pid.endswith(f"@{name}")]
    if in_use:
        raise PluginError(f"plugins from {name} are still installed: {', '.join(sorted(in_use))}")
    if not isinstance(record.get("source"), str):
        shutil.rmtree(record.get("installLocation") or "", ignore_errors=True)
    store.save_marketplaces(known)


def marketplace_entries(name: str) -> list[dict[str, Any]]:
    record = store.marketplaces().get(name)
    if record is None:
        raise PluginError(f"no marketplace named {name}")
    return read_marketplace(Path(record["installLocation"]))["plugins"]


def list_marketplaces() -> list[dict[str, Any]]:
    out = []
    for name, record in sorted(store.marketplaces().items()):
        try:
            count, error = len(marketplace_entries(name)), ""
        except PluginError as exc:
            count, error = 0, str(exc)
        out.append({"name": name, "plugins": count, "error": error, **record})
    return out
