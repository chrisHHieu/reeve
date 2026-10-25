"""Installing, updating and removing plugins; adding marketplaces (design §7, §8).

One set of functions for every surface — GUI, CLI, API — so the consent a person gives is
the same everywhere. An install is two steps: `stage_install` fetches into a staging
folder and returns what a person must see (`ConsentSummary`); `confirm_install` takes only
the staging TOKEN and installs what was staged, so nothing can change between the preview
and the yes. A stage older than an hour is swept.

Only a person reaches these: no tool exposes them to the model, and every state file is a
protected path (design §9.6).
"""

from __future__ import annotations

import re
import secrets as _secrets
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from . import store
from .manifest import MANIFEST_PATHS, read_manifest
from .marketplace import PluginError, _now, marketplace_entries
from .consent import consent_summary, diff_summaries
from .store import list_installed, sweep_orphans  # noqa: F401 (re-exported)
from .sources import Downloader, SourceError, computed_hash, fetch

STAGE_TTL_SECONDS = 3600


# -- staging and consent ----------------------------------------------------------------------------


def _staged_root() -> Path:
    return store.plugins_dir() / "staged"


def _sweep_stages() -> None:
    root = _staged_root()
    if not root.is_dir():
        return
    for stage in root.iterdir():
        try:
            if time.time() - stage.stat().st_mtime > STAGE_TTL_SECONDS:
                shutil.rmtree(stage, ignore_errors=True)
        except OSError:
            continue


def _entry_for(plugin_id: str) -> tuple[str, str, dict[str, Any], Path]:
    if "@" not in plugin_id:
        raise PluginError(f"{plugin_id!r} is not <plugin>@<marketplace>")
    name, market = plugin_id.rsplit("@", 1)
    record = store.marketplaces().get(market)
    if record is None:
        raise PluginError(f"no marketplace named {market} — add it first")
    entry = next((e for e in marketplace_entries(market) if e.get("name") == name), None)
    if entry is None:
        raise PluginError(f"{market} has no plugin named {name}")
    return name, market, entry, Path(record["installLocation"])


def _version(manifest_version: str, entry: dict[str, Any], sha: str) -> str:
    """Design §8.3: the manifest's, else the entry's, else the source's identity."""
    for value in (manifest_version, str(entry.get("version") or "")):
        if value:
            return re.sub(r"[^A-Za-z0-9._+-]", "-", value)
    return sha[:12] if sha else "unknown"


def stage_install(
    plugin_id: str,
    *,
    scope: str = "user",
    workspace: Optional[str] = None,
    download: Optional[Downloader] = None,
    update: bool = False,
    _needed_by: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Fetch `<plugin>@<marketplace>` into staging and say what it is. Installs nothing.
    Missing dependencies are staged with it, so one consent covers the set."""
    if scope not in ("user", "project", "local"):
        raise PluginError(f"cannot install into the {scope!r} scope")
    if scope != "user" and not workspace:
        raise PluginError(f"a {scope} install needs a workspace")
    _sweep_stages()
    name, market, entry, market_root = _entry_for(plugin_id)
    token = _secrets.token_urlsafe(12)
    stage = _staged_root() / token
    source = entry.get("source")
    in_place = ""
    try:
        if isinstance(source, str) and (source.startswith("./") or source == ".") and not store.marketplaces()[market].get("sha"):
            # A local marketplace's relative plugin loads where it is (design §7.2).
            path = (market_root / source).resolve()
            if not path.is_dir() or (path != market_root.resolve() and market_root.resolve() not in path.parents):
                raise PluginError(f"{source} is not a folder in {market}")
            root, detail, sha, in_place = path, str(path), "", str(path)
            stage.mkdir(parents=True)
        else:
            fetched = fetch(source, stage / "plugin", marketplace_root=market_root, download=download)
            root, detail, sha = fetched.path, fetched.detail, fetched.sha
    except SourceError as exc:
        shutil.rmtree(stage, ignore_errors=True)
        raise PluginError(str(exc)) from exc
    manifest = read_manifest(root, entry=entry)
    version = _version(manifest.version, entry, sha)
    try:
        summary = consent_summary(root, plugin_id=plugin_id, version=version, fetched_detail=detail, sha=sha, entry=entry)
    except PluginError:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    if update:
        current = store.install_record(plugin_id)
        summary["current_version"] = (current or {}).get("version", "")
    from .deps import dependency_ids

    dep_tokens: list[str] = []
    dependencies: list[dict[str, Any]] = []
    try:
        for dep in dependency_ids(manifest.raw, plugin_id):
            if dep in _needed_by or dep == plugin_id:
                raise PluginError(f"{plugin_id} and {dep} depend on each other")
            if store.install_record(dep) is not None:
                dependencies.append({"id": dep, "installed": True})
                continue
            staged = stage_install(
                dep, scope=scope, workspace=workspace, download=download, _needed_by=_needed_by | {plugin_id}
            )
            dep_tokens.append(staged["token"])
            dependencies.append({"id": dep, "installed": False, "consent": staged["consent"]})
    except PluginError:
        for dep_token in dep_tokens:
            cancel_install(dep_token)
        shutil.rmtree(stage, ignore_errors=True)
        raise
    summary["dependencies"] = dependencies
    store.write_json(
        stage / "stage.json",
        {
            "id": plugin_id,
            "name": name,
            "marketplace": market,
            "scope": scope,
            "workspace": workspace or "",
            "version": version,
            "source": source,
            "sha": sha,
            "entry": entry,
            "in_place": in_place,
            "summary": summary,
            "dep_tokens": dep_tokens,
        },
    )
    return {"token": token, "consent": summary}


def cancel_install(token: str) -> None:
    stage = _stage_dir(token)
    for dep_token in store.read_json(stage / "stage.json", {}).get("dep_tokens") or []:
        shutil.rmtree(_stage_dir(dep_token), ignore_errors=True)
    shutil.rmtree(stage, ignore_errors=True)


def _stage_dir(token: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", token or ""):
        raise PluginError("not a staging token")
    return _staged_root() / token


def confirm_install(token: str, user_config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Install exactly what `stage_install` previewed under this token, with the settings
    the person gave (a required one missing refuses the install; nothing is written)."""
    from . import config

    stage = _stage_dir(token)
    meta = store.read_json(stage / "stage.json", {})
    if not meta:
        raise PluginError("that preview has expired — start the install again")
    if meta.get("kind") == "consent":
        return _confirm_consent(stage, meta)
    plugin_id, version = str(meta["id"]), str(meta["version"])
    preview_root = Path(meta["in_place"]) if meta.get("in_place") else stage / "plugin"
    options = config.options_of(read_manifest(preview_root, entry=meta.get("entry")).raw)[0]
    clean, problems = config.validate(options, user_config or {})
    already = config.values(plugin_id, options, include_sensitive=True)
    unset = [k for k, o in options.items() if o.get("required") and k not in clean and k not in already]
    if problems or unset:
        raise PluginError("; ".join(problems + ([f"needs a value for {', '.join(unset)}"] if unset else [])))
    for dep_token in meta.get("dep_tokens") or []:
        confirm_install(dep_token)
    staged_digest = str((meta.get("summary") or {}).get("executable_digest") or "")
    if meta.get("in_place"):
        root = Path(meta["in_place"])
        install_path = str(root)
        # A folder loaded in place can change after the preview: consent covers only the
        # code the person read.
        from .consent import executable_surface

        now = executable_surface(root, read_manifest(root, entry=meta.get("entry")).raw, str(meta["name"]))[1]
        if now != staged_digest:
            raise PluginError("the plugin's code changed after the preview — start the install again")
    else:
        rel = Path("cache") / store.safe_id(meta["marketplace"]) / store.safe_id(meta["name"]) / version
        target = store.plugins_dir() / rel
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stage / "plugin"), str(target))
        root, install_path = target, rel.as_posix()
    # The plugin's own files, hashed before anything is added to them (its Node packages).
    content_hash = computed_hash(root)
    warnings: list[str] = []
    if staged_digest and not meta.get("in_place"):
        # Its Node packages, only into the installed copy — a local folder is its author's
        # to set up — and only for a plugin whose code runs (design §8.5).
        from .node import install_node_packages

        warning = install_node_packages(root)
        if warning:
            warnings.append(warning)
    records = store.installed()
    kept = [r for r in records.get(plugin_id, []) if r.get("scope") != meta["scope"]]
    previous = next((r for r in records.get(plugin_id, []) if r.get("scope") == meta["scope"]), None)
    record = {
        "scope": meta["scope"],
        "version": version,
        "installPath": install_path,
        "source": meta["source"],
        "entry": meta["entry"],
        "computedHash": content_hash,
        "installedAt": (previous or {}).get("installedAt") or _now(),
        "updatedAt": _now(),
        **({"workspace": meta["workspace"]} if meta.get("workspace") else {}),
        **({"sha": meta["sha"]} if meta.get("sha") else {}),
        **({"warnings": warnings} if warnings else {}),
    }
    records[plugin_id] = [*kept, record]
    # An update that dropped a persona takes it away with the old version.
    store.save_installed(records)
    if previous and previous.get("installPath") != install_path:
        store.orphan(previous)
    store.set_enabled(plugin_id, True, meta["scope"], meta.get("workspace") or None)
    if staged_digest:
        # The yes covers exactly the code that was shown (design §9.3): everywhere for a
        # person's own install, this workspace for a project's.
        store.record_consent(
            plugin_id,
            staged_digest,
            workspace=(meta.get("workspace") or None) if meta["scope"] == "project" else None,
        )
    if clean:
        config.save(plugin_id, options, clean)
    from .updates import clear_waiting

    clear_waiting(plugin_id)
    shutil.rmtree(stage, ignore_errors=True)
    return {"id": plugin_id, "version": version, "scope": meta["scope"], **({"warnings": warnings} if warnings else {})}



def uninstall(
    plugin_id: str, *, scope: str = "user", workspace: Optional[str] = None, keep_data: bool = False
) -> dict[str, Any]:
    records = store.installed()
    mine = [r for r in records.get(plugin_id, []) if r.get("scope") == scope]
    if not mine:
        raise PluginError(f"{plugin_id} is not installed at {scope} scope")
    from .resolve import resolve_plugins

    needed_by = [p.id for p in resolve_plugins(workspace=workspace).plugins if plugin_id in p.dependencies]
    if needed_by:
        raise PluginError(f"{', '.join(needed_by)} needs {plugin_id}; remove that first")
    rest = [r for r in records.get(plugin_id, []) if r.get("scope") != scope]
    if rest:
        records[plugin_id] = rest
    else:
        records.pop(plugin_id, None)
    store.save_installed(records)
    store.forget_enabled(plugin_id, scope, workspace)
    for record in mine:
        if not rest:
            store.orphan(record)
    removed_data = False
    if not rest:
        from .config import forget

        forget(plugin_id)
        store.forget_consent(plugin_id)
        from . import updates

        updates.forget(plugin_id)
    if not rest and not keep_data:
        from .resolve import data_dir

        shutil.rmtree(data_dir(plugin_id), ignore_errors=True)
        removed_data = True
    return {"id": plugin_id, "scope": scope, "data_removed": removed_data}


def stage_consent(plugin_id: str, *, workspace: Optional[str] = None) -> dict[str, Any]:
    """Show an installed plugin's current code for a yes: its code changed since the last
    one (a local folder edited, an install touched by hand), or a project turned it on in
    a workspace that has not agreed yet (design §9.3, §9.4)."""
    from .consent import consent_summary

    record = store.install_record(plugin_id)
    if record is None:
        raise PluginError(f"{plugin_id} is not installed")
    root = store.install_root(record)
    summary = consent_summary(
        root,
        plugin_id=plugin_id,
        version=str(record.get("version") or ""),
        fetched_detail=str(root),
        sha=str(record.get("sha") or ""),
        entry=record.get("entry") or {},
    )
    if not summary.get("executable_digest"):
        raise PluginError(f"{plugin_id} runs no code; there is nothing to agree to")
    _sweep_stages()
    token = _secrets.token_urlsafe(12)
    stage = _staged_root() / token
    stage.mkdir(parents=True)
    store.write_json(
        stage / "stage.json",
        {"kind": "consent", "id": plugin_id, "workspace": workspace or "", "summary": summary, "root": str(root), "entry": record.get("entry") or {}},
    )
    return {"token": token, "consent": summary}


def _confirm_consent(stage: Path, meta: dict[str, Any]) -> dict[str, Any]:
    from .consent import executable_surface

    root = Path(meta["root"])
    digest = str(meta["summary"]["executable_digest"])
    now = executable_surface(root, read_manifest(root, entry=meta.get("entry")).raw, str(meta["summary"]["name"]))[1]
    if now != digest:
        shutil.rmtree(stage, ignore_errors=True)
        raise PluginError("the plugin's code changed again after the preview — review it again")
    store.record_consent(str(meta["id"]), digest, workspace=meta.get("workspace") or None)
    shutil.rmtree(stage, ignore_errors=True)
    return {"id": meta["id"], "consented": digest}


def stage_update(plugin_id: str, *, download: Optional[Downloader] = None) -> dict[str, Any]:
    """Fetch the plugin's current version from its marketplace (refreshed first) and stage
    it with a diff against what is installed; `up_to_date` when nothing moved. The
    person confirms with the same token flow as an install."""
    from .marketplace import refresh_marketplace

    record = store.install_record(plugin_id)
    if record is None:
        raise PluginError(f"{plugin_id} is not installed")
    market = plugin_id.rsplit("@", 1)[1]
    if not isinstance((store.marketplaces().get(market) or {}).get("source"), str):
        refresh_marketplace(market, download=download)
    staged = stage_install(
        plugin_id,
        scope=str(record.get("scope") or "user"),
        workspace=record.get("workspace") or None,
        download=download,
        update=True,
    )
    if staged["consent"]["version"] == record.get("version"):
        cancel_install(staged["token"])
        return {"up_to_date": True, "version": record.get("version")}
    current = consent_summary(
        store.install_root(record),
        plugin_id=plugin_id,
        version=str(record.get("version")),
        fetched_detail="",
        sha=str(record.get("sha") or ""),
        entry=record.get("entry") or {},
    )
    return {**staged, "diff": diff_summaries(current, staged["consent"])}
