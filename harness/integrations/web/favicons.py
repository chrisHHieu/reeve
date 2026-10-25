"""A search result's site mark, fetched once by the search that found it and kept on this computer.

The search card drew a letter on a tint, on purpose: an `<img>` pointing at a site's favicon would
make the GUI call every result's site, past the approval a search passes. So the mark is
fetched here instead, inside the `web_search` call the person allowed: at the address the engine
reported (Tavily's `favicon`), else at the site's `/favicon.ico` (Exa's API stopped reporting one,
measured 2026-10-11, and DuckDuckGo never did); only for a host not seen before, in parallel, within a
short budget, never more than a small raster image. The GUI asks the local server for it by host.

What a site sees: one request for its icon, the first time a search returns it. A host whose icon
could not be had is remembered for a week, so a dead icon is not asked for on every search. The
address is a third party's data, so it goes through the same address guard as `web_fetch`: every
hop is checked and pinned, and a loopback or private address is refused before any connection.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlsplit

from harness.safety.secrets.locations import state_dir

#: Big enough for any real favicon (most are 1-15 KB; a multi-size .ico runs to ~120 KB, vtv.vn's
#: measured 2026-10-11), small enough that a page posing as one costs nothing.
MAX_BYTES = 256 * 1024
#: The whole fetch rides inside a search the person is waiting on: a slow site goes without.
BUDGET_S = 2.0
#: Raster only. An SVG is a document that can carry script, and the server would hand it out
#: from its own origin.
_TYPES = {
    "image/png": "png",
    "image/x-icon": "ico",
    "image/vnd.microsoft.icon": "ico",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}
MEDIA = {ext: media for media, ext in _TYPES.items()}
MEDIA["ico"] = "image/x-icon"
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_MISS_TTL_S = 7 * 24 * 3600


def _dir() -> Path:
    return state_dir() / "favicons"


def host_of(url: str) -> Optional[str]:
    """The host a mark is kept under (no `www.`), or None for anything that is not a plain host."""
    try:
        host = (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return None
    host = host[4:] if host.startswith("www.") else host
    return host if _HOST.match(host) else None


def cached(host: str) -> Optional[Path]:
    """The kept icon for `host`, if there is one."""
    if not _HOST.match(host or ""):
        return None
    for ext in set(_TYPES.values()):
        path = _dir() / f"{host}.{ext}"
        if path.is_file():
            return path
    return None


def _missed_recently(host: str) -> bool:
    marker = _dir() / f"{host}.miss"
    try:
        return time.time() - marker.stat().st_mtime < _MISS_TTL_S
    except OSError:
        return False


def _open(url: str):
    """GET `url` through the address guard, streamed; the caller reads a bounded body and closes."""
    import httpx

    from .guard import get_checked

    client = httpx.Client(follow_redirects=False, timeout=BUDGET_S)
    resp = get_checked(client, url, stream=True)
    resp.extensions["client"] = client  # closed with the response
    return resp


def _fetch(host: str, url: str) -> None:
    """Keep `host`'s icon from `url`, else from its parent domains' `/favicon.ico`: a subdomain
    often has none of its own (docs.celeris.ai, techcommunity.microsoft.com, measured 2026-10-11)
    and wears the site's."""
    labels = host.split(".")
    parents = [".".join(labels[i:]) for i in range(1, len(labels) - 1)]
    for candidate in [url] + [f"https://{parent}/favicon.ico" for parent in parents]:
        if _fetch_one(host, candidate):
            return
    (_dir() / f"{host}.miss").touch()


def _fetch_one(host: str, url: str) -> bool:
    folder = _dir()
    folder.mkdir(parents=True, exist_ok=True)
    resp = None
    try:
        if urlsplit(url).scheme not in ("http", "https"):
            raise ValueError("not a web address")
        resp = _open(url)
        media = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        ext = _TYPES.get(media)
        if resp.status_code != 200 or ext is None:
            raise ValueError("not an icon")
        body = b""
        for chunk in resp.iter_bytes():
            body += chunk
            if len(body) > MAX_BYTES:
                raise ValueError("too large")
        if not body:
            raise ValueError("empty")
        part = folder / f"{host}.{ext}.part"
        part.write_bytes(body)
        part.replace(folder / f"{host}.{ext}")
        return True
    except Exception:  # noqa: BLE001 — a missing mark is a letter tile, never an error
        return False
    finally:
        if resp is not None:
            for closable in (resp, getattr(resp, "extensions", {}).get("client")):
                close = getattr(closable, "close", None)
                if callable(close):
                    close()


def prefetch(pairs: Iterable[tuple[str, str]], budget_s: float = BUDGET_S) -> set[str]:
    """Fetch the icons of hosts not kept yet; return the hosts that have one now.

    `pairs` is (result URL, favicon URL or None); with none, the site's `/favicon.ico` is asked.
    Waits at most `budget_s`; a fetch still running then finishes on its own and serves the next
    search.
    """
    wanted: dict[str, str] = {}
    have: set[str] = set()
    for page, icon in pairs:
        host = host_of(page)
        if not host:
            continue
        icon = icon or f"https://{host}/favicon.ico"
        if cached(host):
            have.add(host)
        elif host not in wanted and not _missed_recently(host):
            wanted[host] = icon
    if wanted:
        pool = ThreadPoolExecutor(max_workers=min(8, len(wanted)))
        futures = [pool.submit(_fetch, host, icon) for host, icon in wanted.items()]
        wait(futures, timeout=budget_s)
        pool.shutdown(wait=False)
        have |= {host for host in wanted if cached(host)}
    return have
