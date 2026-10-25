"""The one agent a session runs.

Reeve has one agent (2026-10-12): the coding agent built by `reeve_agent`. There is no persona
to pick, install or switch to. A session or an automation recorded under another id (a persona
that shipped once, `code`, a team's worker or lead, an installed one) resolves to this agent, so
its conversation opens and goes on; the record keeps the id it was written with.
"""

from __future__ import annotations

from typing import Optional

from .base import Agent
from .reeve import reeve_agent

#: The id of the one agent, as session records and the socket carry it.
DEFAULT_AGENT = "reeve"


def resolve_agent_id(name: Optional[str] = None) -> str:
    """The agent a stored or requested id means today: always the one there is."""
    return DEFAULT_AGENT


def get_agent(name: Optional[str] = None) -> Agent:
    """The agent for a session, whatever id its record names."""
    return reeve_agent()
