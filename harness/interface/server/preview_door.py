"""The preview door: a second, small listener that serves the files a person opened to look at.

Why a second listener. The Files tab shows a page the session wrote, and a page is
only itself when its own folder answers: `style.css`, `app.js`, the next page a link names, a
place to keep its state. Served from the API's own address, that page would share an origin
with every route that drives a session. Served from nowhere (the old `srcdoc` box), none of
its files load. So it gets an address of its own that knows nothing but how to hand out granted
folders. A `<video>` or `<audio>` element comes here too: it cannot carry the sidecar token.

What it will not do, whatever it is asked:

- answer for a key it did not issue (keys are random, issued by the token-guarded API);
- leave the granted folder, follow a link out of it, or serve a dotfile;
- serve a file whose type is not on `SERVED`, so `.env`, keys and source files are never read
  through a page;
- anything but GET and HEAD.

A page is sent with a Content-Security-Policy. By default it names this address alone, so the
page runs and cannot fetch from or send to the internet; a grant made with `internet` lifts it,
and only a person makes that grant, through the API.
"""

from __future__ import annotations

import re
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from .app.access import ORIGIN_PATTERN
from .preview_design import DESIGN_BRIDGE

#: What a page and a media element may be handed, with the type each is sent as. An explicit
#: table: `mimetypes` reads the Windows registry, where `.js` can be `text/plain` and a module
#: script then refuses to run.
SERVED: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".tsv": "text/tab-separated-values; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".wasm": "application/wasm",
    ".pdf": "application/pdf",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".ogv": "video/ogg",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    # Documents the Files tab draws itself, from their bytes.
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_PAGES = (".html", ".htm")
_MAX_GRANTS = 64
#: Who may put a served page in a frame: the app's own window, nothing else.
_FRAMERS = "reeve://app http://localhost:* http://127.0.0.1:* http://tauri.localhost https://tauri.localhost tauri://localhost"

# Told to the app's window, which draws the address line and the "blocked" count. The page
# cannot be trusted to say true things, so the app treats every field as text to show.
_BRIDGE = """<script>(function(){
if(window.top===window)return;
var say=function(m){m.reevePreview=1;try{window.top.postMessage(m,'*');}catch(e){}};
var here=function(){say({type:'page',path:location.pathname+location.search+location.hash,title:document.title});};
here();
window.addEventListener('load',here);
window.addEventListener('hashchange',here);
document.addEventListener('securitypolicyviolation',function(e){say({type:'blocked',url:String(e.blockedURI||''),what:String(e.effectiveDirective||'')});});
window.addEventListener('message',function(e){var d=e.data||{};
if(d.reevePreviewDo==='back')history.back();
else if(d.reevePreviewDo==='forward')history.forward();
else if(d.reevePreviewDo==='reload')location.reload();});
})();</script>"""
_HEAD_RE = re.compile(rb"<head[^>]*>", re.IGNORECASE)
_CHARSET_RE = re.compile(rb"<meta[^>]+charset", re.IGNORECASE)


@dataclass(frozen=True)
class Grant:
    root: Path
    internet: bool
    #: When set, the one file this grant serves (a media element or a document's bytes).
    only: Optional[str]


def page_policy(origin: str, internet: bool) -> str:
    """The Content-Security-Policy a served file is sent with."""
    if internet:
        # The person let this page use the internet: only who may frame it stays fixed.
        return f"frame-ancestors {_FRAMERS}"
    return (
        f"default-src {origin} 'unsafe-inline' 'unsafe-eval' data: blob:; "
        f"connect-src {origin}; form-action {origin}; frame-src {origin}; base-uri {origin}; "
        f"object-src 'none'; frame-ancestors {_FRAMERS}"
    )


def _names_its_encoding(body: bytes) -> bool:
    """Whether a page says what it is saved as (a BOM or a `<meta charset>` near its top), or
    is not UTF-8 at all, in which case the browser's own guess is the better one."""
    if body.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")) or _CHARSET_RE.search(body[:2048]):
        return True
    try:
        body.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


class PreviewDoor:
    """Grants, and the listener that answers for them. The listener starts with the first
    grant, so a session that never previews a page never opens the port."""

    def __init__(self) -> None:
        self._grants: dict[str, Grant] = {}
        self._lock = threading.Lock()
        self._server = None
        self._port: Optional[int] = None

    # -- grants ---------------------------------------------------------------------------

    def grant(self, root: Path, *, internet: bool = False, only: Optional[str] = None) -> str:
        """A key for `root` (already resolved, already known to belong to the session)."""
        key = secrets.token_urlsafe(24)
        with self._lock:
            self._grants[key] = Grant(root=root, internet=bool(internet), only=only)
            while len(self._grants) > _MAX_GRANTS:  # oldest first: dicts keep insertion order
                self._grants.pop(next(iter(self._grants)))
        return key

    def revoke(self, key: str) -> None:
        with self._lock:
            self._grants.pop(key, None)

    def url(self, key: str, rel: str) -> str:
        return f"{self.origin}/s/{key}/{quote(rel)}"

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port()}"

    @property
    def listening(self) -> Optional[str]:
        """The origin when the listener is up, without starting it."""
        return f"http://127.0.0.1:{self._port}" if self._port else None

    # -- the listener ---------------------------------------------------------------------

    def port(self) -> int:
        with self._lock:
            if self._port is not None:
                return self._port
            import uvicorn

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
            server = uvicorn.Server(uvicorn.Config(self.app(), log_level="warning", access_log=False))
            threading.Thread(target=server.run, kwargs={"sockets": [sock]}, name="reeve-preview-door", daemon=True).start()
            deadline = time.time() + 5.0
            while not server.started and time.time() < deadline:
                time.sleep(0.02)
            self._server, self._port = server, port
            return port

    def close(self) -> None:
        with self._lock:
            server, self._server, self._port = self._server, None, None
            self._grants.clear()
        if server is not None:
            server.should_exit = True

    def app(self) -> Starlette:
        return Starlette(routes=[Route("/s/{key}/{path:path}", self._serve, methods=["GET", "HEAD"])])

    def _target(self, grant: Grant, rel: str) -> Optional[Path]:
        """The file `rel` names inside the grant, or None for anything it must not serve."""
        if "\\" in rel or "\x00" in rel:
            return None
        parts = PurePosixPath(rel).parts
        # No dotfile and no "..": a part that starts with a dot is never served.
        if not parts or any(part.startswith((".", "/")) or ":" in part for part in parts):
            return None
        if grant.only is not None and "/".join(parts) != grant.only:
            return None
        try:
            target = grant.root.joinpath(*parts).resolve()
        except OSError:
            return None
        # `resolve` follows links: a link that points out of the folder is out of the folder.
        if not target.is_relative_to(grant.root) or not target.is_file():
            return None
        if target.suffix.lower() not in SERVED:
            return None
        return target

    async def _serve(self, request: Request) -> Response:
        port = self._port
        # A page elsewhere that points a name at 127.0.0.1 does not reach a grant by it.
        if port is not None and request.headers.get("host", "") != f"127.0.0.1:{port}":
            return PlainTextResponse("Not found", status_code=404)
        key = request.path_params["key"]
        rel = request.path_params["path"]
        with self._lock:
            grant = self._grants.get(key)
        if grant is None:
            return PlainTextResponse("Not found", status_code=404)
        if grant.only is None and (rel == "" or rel.endswith("/")):
            rel = f"{rel}index.html"
        elif grant.only is None and (grant.root / rel).is_dir():
            # A folder's own address ends with a slash, or its page's relative links miss.
            return RedirectResponse(f"/s/{key}/{quote(rel)}/")
        target = self._target(grant, rel)
        if target is None:
            return PlainTextResponse("Not found", status_code=404)
        origin = f"http://127.0.0.1:{port}" if port is not None else str(request.base_url).rstrip("/")
        headers = {
            "Content-Security-Policy": page_policy(origin, grant.internet),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        }
        asker = request.headers.get("origin")
        if asker and ORIGIN_PATTERN.match(asker):
            # The app's window reads a document's bytes from here to draw it.
            headers["Access-Control-Allow-Origin"] = asker
            headers["Vary"] = "Origin"
        media_type = SERVED[target.suffix.lower()]
        if target.suffix.lower() in _PAGES:
            try:
                body = target.read_bytes()
            except OSError:
                return PlainTextResponse("Not found", status_code=404)
            head = _HEAD_RE.search(body)
            cut = head.end() if head else 0
            # A header beats a `<meta charset>`, so the encoding is named here only when the
            # page does not name its own: a page saved as Windows-1258 that says so is read as
            # it says, and a UTF-8 page that says nothing is not read as Windows-1252.
            headers["Content-Type"] = "text/html" if _names_its_encoding(body) else "text/html; charset=utf-8"
            # The design bridge (point, pin, retype) rides with it, off until the app turns a mode on.
            body = body[:cut] + _BRIDGE.encode("utf-8") + DESIGN_BRIDGE.encode("utf-8") + body[cut:]
            return Response(body, headers=headers)
        # Range requests are answered by FileResponse: a video can be scrubbed.
        return FileResponse(target, media_type=media_type.split(";")[0], headers=headers)
