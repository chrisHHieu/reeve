"""The toolsets of GitHub's own MCP server that Reeve offers, and what each one needs.

GitHub's remote server (`https://api.githubcopilot.com/mcp/`) groups its ~80 tools into
toolsets and serves only its default five unless the client names others in the
`X-MCP-Toolsets` header. Reeve used to send no header, so a connected account had a
fraction of what the server offers (found 2026-09-30 comparing the repo's tool list with a
live connection). The owner's call: use the server's tools, do not rebuild them, and let
the user turn on the groups a job needs.

Two things are decided here and nowhere else:

* which toolsets exist and which are on for a new connection — the five the server always
  serves, plus the ones the base grant (`repo read:org`) already covers;
* which OAuth scopes a toolset needs beyond that grant. A toolset whose scope the token
  lacks is offered with a "grant access" step instead of being switched on to fail with a
  403 on the first call.

The chosen ids live in the connector profile (`mcp_toolsets`), not in `mcp.json`: like the
url, the header is the descriptor's decision re-applied at every session build.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

#: What every connection is granted; a toolset listing no extra scope works with this.
BASE_SCOPES: tuple[str, ...] = ("repo", "read:org")


@dataclass(frozen=True)
class Toolset:
    id: str
    label: str
    description: str
    #: Scopes beyond `BASE_SCOPES` the tools of this set need.
    scopes: tuple[str, ...] = ()
    #: On for a new connection. Scope-hungry sets start off: asking for a permission the
    #: user did not knowingly want is what gets an OAuth App refused.
    default: bool = False
    #: The server serves it regardless; switching it off would only hide tools it exposes.
    locked: bool = False


TOOLSETS: tuple[Toolset, ...] = (
    Toolset("context", "Your account", "Who you are, your teams.", default=True, locked=True),
    Toolset("repos", "Repositories", "Files, branches, commits, tags, releases, code search.", default=True, locked=True),
    Toolset("issues", "Issues", "Read, search, create and comment on issues.", default=True, locked=True),
    Toolset("pull_requests", "Pull requests", "Read diffs and checks, open, review and merge.", default=True, locked=True),
    Toolset("users", "Users", "Look up users and their repositories.", default=True, locked=True),
    Toolset("actions", "Actions", "Workflow runs, job logs, re-run and trigger workflows.", default=True),
    Toolset("discussions", "Discussions", "Read and reply to discussions.", default=True),
    Toolset("labels", "Labels", "Read and manage labels.", default=True),
    Toolset("orgs", "Organizations", "Search organizations.", default=True),
    Toolset("security_advisories", "Security advisories", "Public and repository advisories.", default=True),
    Toolset("code_security", "Code scanning", "Code scanning alerts.", scopes=("security_events",)),
    Toolset("secret_protection", "Secret scanning", "Secret scanning alerts.", scopes=("security_events",)),
    Toolset("dependabot", "Dependabot", "Dependabot alerts.", scopes=("security_events",)),
    Toolset("notifications", "Notifications", "Read, dismiss and manage notifications.", scopes=("notifications",)),
    Toolset("projects", "Projects", "Read and update project boards.", scopes=("project",)),
    Toolset("gists", "Gists", "Read and write gists.", scopes=("gist",)),
)

_BY_ID = {t.id: t for t in TOOLSETS}

#: The header GitHub reads. Unknown ids are ignored by the server, so a set retired on its
#: side cannot break the connection.
HEADER = "X-MCP-Toolsets"


def default_ids() -> list[str]:
    return [t.id for t in TOOLSETS if t.default]


def enabled_ids(profile: Optional[dict[str, Any]]) -> list[str]:
    """The toolsets this connection serves: the profile's choice, locked sets always in.

    Unknown ids are dropped and order follows the catalog, so the header is stable for a
    given choice — it is part of the per-session MCP signature.
    """
    chosen = (profile or {}).get("mcp_toolsets")
    if not isinstance(chosen, (list, tuple)):
        return default_ids()
    wanted = {str(i) for i in chosen}
    return [t.id for t in TOOLSETS if t.locked or t.id in wanted]


def header_for(profile: Optional[dict[str, Any]]) -> dict[str, str]:
    return {HEADER: ",".join(enabled_ids(profile))}


def scopes_for(ids: Iterable[str]) -> tuple[str, ...]:
    """The scope set a token needs to serve `ids`: the base grant plus each set's extras."""
    out = list(BASE_SCOPES)
    for i in ids:
        for scope in getattr(_BY_ID.get(i), "scopes", ()):
            if scope not in out:
                out.append(scope)
    return tuple(out)


def scope_string(ids: Iterable[str]) -> str:
    """The `scope` parameter of the device request (GitHub takes them space-separated)."""
    return " ".join(scopes_for(ids))


def parse_granted(scope: Any) -> set[str]:
    """A token's `scope` field: GitHub returns it comma-separated, RFC 6749 space-separated."""
    return {s for s in str(scope or "").replace(",", " ").split() if s}


def missing_scopes(ids: Iterable[str], granted: Optional[set[str]]) -> list[str]:
    """Scopes `ids` need that the token lacks. None = unknown (a pasted token) → none.

    `repo` also covers what GitHub folds under it, which is why only the extras are ever
    listed as missing.
    """
    if granted is None:
        return []
    return [s for s in scopes_for(ids) if s not in granted]


def catalog(profile: Optional[dict[str, Any]], granted: Optional[set[str]]) -> list[dict[str, Any]]:
    """The rows the Connectors page draws: one per toolset, its state and what it needs."""
    on = set(enabled_ids(profile))
    rows = []
    for t in TOOLSETS:
        need = [s for s in t.scopes if granted is not None and s not in granted]
        rows.append(
            {
                "id": t.id,
                "label": t.label,
                "description": t.description,
                "enabled": t.id in on,
                "locked": t.locked,
                # A set that is on but whose scope the token lacks serves tools that fail.
                "needs_scopes": need,
            }
        )
    return rows
