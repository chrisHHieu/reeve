"""Two facts a session keeps about itself: where the person already worked when it began, and each
time content from outside the machine came in after.

Python works both out; no model is asked. Today neither one decides anything: the first is text a
future judge would be shown, the second goes to the audit log and nothing reads it. They are kept
now so that, when the question "would this have changed a verdict?" comes, it is answered by
replaying recorded sessions in shadow instead of argued from memory.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

from harness.platform.spawn import run as spawn_run

# The tool categories whose results bring in what someone outside the machine wrote. By category,
# not by tool name, so a connector added later is counted from its first day:
#   web        web_fetch and web_search
#   connector  mail, chat, documents: anything that reads another company's service
#   mcp        a third-party server, whose sources nobody here can know
# Not counted: `search` (a local grep), `filesystem`, `git`, `shell`, and `messaging`, whose tools
# send out rather than bring in. Local reads stay out on purpose, or every turn would count and the
# signal would mean nothing. The price is written in the spec: a planted README in a cloned
# repository brings its words in with no record at all.
INGESTING_CATEGORIES = frozenset({"web", "connector", "mcp"})

_GIT_SECONDS = 5


def is_ingesting(metadata: Any) -> bool:
    """Whether a tool of this metadata can return words from outside the machine."""
    return getattr(metadata, "category", "") in INGESTING_CATEGORIES


def ingestion_source(arguments: dict[str, Any] | None) -> str:
    """Where the content came from, as little as says it: the host of the call's `url`, else `-`.
    Never the content and never the whole address: a query string is just where a payload rides."""
    url = str((arguments or {}).get("url", "")).strip()
    host = urlsplit(url).hostname if url else None
    return host.lower() if host else "-"


def _git_remotes(cwd: Path) -> tuple[tuple[str, str], ...]:
    """Each remote's `(name, url)`, once: `git remote -v` lists fetch and push apart.

    Nothing here may cost the session. Without git, outside a repository or past the timeout the
    answer is no remotes, and a judge simply has less to go on."""
    try:
        listed = spawn_run(
            ["git", "remote", "-v"], cwd=str(cwd), capture_output=True, encoding="utf-8", errors="replace", timeout=_GIT_SECONDS, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    remotes: dict[str, str] = {}
    for words in (line.split() for line in listed.stdout.splitlines()):
        if len(words) > 1:
            remotes.setdefault(words[0], words[1])
    return tuple(remotes.items())


def _host_of(remote_url: str) -> Optional[str]:
    """A remote's host. `git@host:path` has no scheme, so it is turned into `//host/path` first."""
    shaped = remote_url if "://" in remote_url else "//" + remote_url.replace(":", "/", 1)
    host = urlsplit(shaped).hostname
    return host.lower() if host else None


@dataclass(frozen=True)
class KnownWorld:
    """The folders and remotes the person was already working with when the session began.

    It is taken once and never updated, and that is the point: checked against the live state, an
    agent that ran `git remote add backup https://attacker.net/r.git` would have made its own
    destination familiar; checked against what was there before it acted, it cannot.

    Familiar is all "known" means. Nobody approved any of it; the person has been here before. It is
    never called "trusted" where a model reads it, since a model takes that word as permission
    (`workspace_trust.json` may say trusted: that one is a decision someone made)."""

    roots: tuple[tuple[str, bool], ...] = ()  # (path, writable)
    remotes: tuple[tuple[str, str], ...] = ()  # (name, url)
    hosts: tuple[str, ...] = ()  # kept and not shown: see `render`
    captured_at: float = 0.0

    def render(self) -> str:
        """The block a future judge's prompt would carry, in its cached part; empty when there is
        nothing to say.

        Folders and remotes only. A list of hosts helps only if the judge can tell whether a
        destination is on it, which is a suffix test (`host == d or host.endswith("." + d)`) that
        Python gets right and models do not: `github.com` printed next to a call to
        `github.com.evil.site` invites the wrong answer. Asking "is this the folder or remote I was
        shown?" has no such trap. When hosts are shown one day, it will be as one line Python
        worked out, never as a list for the model to search."""
        rows = [f"  folder   {path}  [{'read-write' if writable else 'read-only'}]" for path, writable in self.roots]
        rows += [f"  remote   {name} -> {url}" for name, url in self.remotes]
        return "\n".join(["KNOWN WORLD (frozen when this session started)", *rows]) if rows else ""


def capture(
    *,
    roots: Iterable[Any] | None = None,
    allowed_domains: Iterable[str] | None = None,
    workspace: Optional[Path] = None,
) -> KnownWorld:
    """The snapshot, taken when the session starts and before the agent has done anything."""
    # A managed root is the harness's own memory folder, which the memory section already names.
    shown = [root for root in roots or () if not getattr(root, "managed", False)]
    folders = tuple((str(getattr(root, "path", root)), bool(getattr(root, "writable", False))) for root in shown)
    where = workspace if workspace is not None else (Path(str(getattr(shown[0], "path", shown[0]))) if shown else None)
    remotes = _git_remotes(where) if where else ()
    hosts = {domain.strip().lower() for domain in allowed_domains or () if domain and domain.strip()}
    hosts |= {host for host in (_host_of(url) for _name, url in remotes) if host}
    return KnownWorld(roots=folders, remotes=remotes, hosts=tuple(sorted(hosts)), captured_at=time.time())


@dataclass
class Ingestion:
    """Content from outside came in: which turn, which tool, from where. Never what it said.

    Before anything reads these, two things hold:

    - A record accuses nobody. An agent following a docs link from an issue and one running an
      injected `curl` leave the same record, because both really did read the issue. It raises the
      bar a later call must clear; telling the two apart is the scope judgment's work.
    - No record is not a clean session. Local reads are not recorded, so a planted file already in
      the workspace leaves nothing here."""

    turn: int
    tool: str
    source: str

    def to_audit(self) -> dict[str, Any]:
        return {"stage": "ingested", "status": "external", "reason": f"turn {self.turn} · {self.source}"}


@dataclass
class SessionFacts:
    """The known world and every arrival since, by turn.

    The engine moves `turn` on at the start of each of the person's turns, so an arrival belongs to
    one. Nothing reads `ingestions` yet; it is the baseline the audit log needs (module docstring)."""

    world: KnownWorld = field(default_factory=KnownWorld)
    turn: int = 0
    ingestions: list[Ingestion] = field(default_factory=list)

    def begin_turn(self) -> None:
        self.turn += 1

    def note(self, tool: str, arguments: dict[str, Any] | None) -> Ingestion:
        arrived = Ingestion(turn=self.turn, tool=tool, source=ingestion_source(arguments))
        self.ingestions.append(arrived)
        return arrived

    def this_turn(self) -> list[Ingestion]:
        return [arrived for arrived in self.ingestions if arrived.turn == self.turn]
