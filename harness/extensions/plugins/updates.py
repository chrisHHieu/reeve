"""Plugins a marketplace withdrew, and updates that apply themselves (design §6.1, §8.4).

**Flagged.** When a marketplace is refreshed and no longer lists a plugin that is installed
from it, the plugin is written to `flagged.json` and stops loading: a withdrawn plugin is
most often withdrawn for a reason its users cannot see, so it is set aside until a person
says otherwise — "keep using it" (`keep_flagged`) or uninstall. Listed again, the flag goes.

**Auto-update** is off for every marketplace until a person turns it on for that one
(`set_auto_update`). Even then an update applies itself only when it runs no code the
person has not already agreed to — the same executable digest (design §9.3), no new
plugin pulled in as a dependency, no new required setting. Anything else is left in
`updates.json` for a person to review through the ordinary update flow. It never runs
when `REEVE_DISABLE_NONESSENTIAL_TRAFFIC` is set, and the server runs it only while no
turn is running.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from . import store
from .marketplace import PluginError, _now, marketplace_entries, refresh_marketplace
from .sources import Downloader

NONESSENTIAL_ENV = "REEVE_DISABLE_NONESSENTIAL_TRAFFIC"


def flag_delisted(market: str) -> list[str]:
    """Match `flagged.json` to what `market` lists now; returns the ids newly flagged."""
    try:
        listed = {str(e.get("name")) for e in marketplace_entries(market)}
    except PluginError:
        return []
    marks = store.flagged()
    newly: list[str] = []
    for plugin_id in store.installed():
        name, _, origin = plugin_id.rpartition("@")
        if origin != market:
            continue
        if name in listed:
            marks.pop(plugin_id, None)
        elif plugin_id not in marks:
            marks[plugin_id] = {"marketplace": market, "flaggedAt": _now()}
            newly.append(plugin_id)
    store.save_flagged(marks)
    return newly


def keep_flagged(plugin_id: str) -> dict[str, Any]:
    """A person chose to keep using a plugin its marketplace withdrew."""
    marks = store.flagged()
    if plugin_id not in marks:
        raise PluginError(f"{plugin_id} is not withdrawn by its marketplace")
    marks[plugin_id] = {**marks[plugin_id], "kept": True}
    store.save_flagged(marks)
    return {"id": plugin_id, "kept": True}


def forget(plugin_id: str) -> None:
    """An uninstalled plugin leaves neither list."""
    marks = store.flagged()
    if marks.pop(plugin_id, None) is not None:
        store.save_flagged(marks)
    clear_waiting(plugin_id)


def clear_waiting(plugin_id: str) -> None:
    waiting = store.waiting_updates()
    if waiting.pop(plugin_id, None) is not None:
        store.save_waiting_updates(waiting)


def set_auto_update(market: str, on: bool) -> dict[str, Any]:
    known = store.marketplaces()
    if market not in known:
        raise PluginError(f"no marketplace named {market}")
    known[market] = {**known[market], "autoUpdate": bool(on)}
    store.save_marketplaces(known)
    return {"name": market, "autoUpdate": bool(on)}


def _why_not_automatic(plugin_id: str, staged: dict[str, Any]) -> str:
    """'' when the staged update may apply itself; else what a person has to look at."""
    consent = staged.get("consent") or {}
    if any(not d.get("installed") for d in consent.get("dependencies") or []):
        return "it now needs another plugin"
    digest = str(consent.get("executable_digest") or "")
    if not digest:
        return ""
    record = store.install_record(plugin_id) or {}
    workspace = record.get("workspace") if record.get("scope") == "project" else None
    if store.consented(plugin_id, digest, workspace=workspace):
        return ""
    return "the code it runs changed"


def auto_update(*, download: Optional[Downloader] = None) -> dict[str, list[Any]]:
    """One pass over the marketplaces with auto-update on. Never raises."""
    from . import manager

    report: dict[str, list[Any]] = {"updated": [], "waiting": [], "flagged": [], "errors": []}
    if os.environ.get(NONESSENTIAL_ENV, "").strip() not in ("", "0"):
        return report
    for market, record in sorted(store.marketplaces().items()):
        if not record.get("autoUpdate"):
            continue
        try:
            refresh_marketplace(market, download=download)
        except PluginError as exc:
            report["errors"].append({"marketplace": market, "error": str(exc)})
            continue
        report["flagged"] += flag_delisted(market)
        marks = store.flagged()
        for plugin_id in sorted(store.installed()):
            if not plugin_id.endswith(f"@{market}") or plugin_id in marks:
                continue
            try:
                staged = manager.stage_update(plugin_id, download=download)
                if staged.get("up_to_date"):
                    clear_waiting(plugin_id)
                    continue
                reason = _why_not_automatic(plugin_id, staged)
                if not reason:
                    try:
                        done = manager.confirm_install(staged["token"])
                    except PluginError as exc:  # a new required setting, most often
                        reason = str(exc)
                    else:
                        clear_waiting(plugin_id)
                        report["updated"].append({"id": plugin_id, "version": done["version"]})
                        continue
                manager.cancel_install(staged["token"])
                waiting = store.waiting_updates()
                waiting[plugin_id] = {
                    "version": str(staged["consent"].get("version") or ""),
                    "reason": reason,
                    "checkedAt": _now(),
                }
                store.save_waiting_updates(waiting)
                report["waiting"].append({"id": plugin_id, "reason": reason})
            except Exception as exc:  # one plugin's failure is its own line
                report["errors"].append({"id": plugin_id, "error": str(exc)})
    return report
