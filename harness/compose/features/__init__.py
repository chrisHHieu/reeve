"""The features a session is assembled from, in the order they run.

The order is the order of the tool list the model is sent, which is part of the cached request
prefix, so it is fixed here and not left to the order of imports. `code_mode` comes after
`delegation` (it offers `workflow` only where `task` exists) and `discovery` comes last (it defers
from every tool the others registered).

A package installed beside Reeve replaces a built-in or adds one through the `reeve.features`
entry points (`installed.py`), with no edit to this list; a person drops one with `[features]
remove` in their config, and turns on an opt-in one (`opt_in = True`) with `[features] add`.
Neither can drop the core: it is not in this list.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from .ask_user import AskUser
from .base import PROMPT_MEMORY, PROMPT_RECAP, Feature, FeatureContext, SessionInputs
from .code_mode import CodeMode
from .connectors import Connectors
from .continuity import Continuity
from .delegation import Delegation
from .deliverables import Deliverables
from .design import Design
from .discovery import Discovery
from .history import History
from .installed import installed, merge
from .lsp import Lsp
from .memory import Memory
from .messaging import Messaging
from .planning import Planning
from .skills import Skills
from .tool_request import ToolRequest
from .web import Web

logger = logging.getLogger(__name__)

FEATURES: tuple[Feature, ...] = (
    Messaging(),
    Deliverables(),
    Design(),
    Lsp(),
    ToolRequest(),
    Connectors(),
    Web(),
    AskUser(),
    Delegation(),
    Continuity(),
    CodeMode(),
    History(),
    Memory(),
    Skills(),
    Planning(),
    Discovery(),
)

FEATURE_IDS: tuple[str, ...] = tuple(feature.id for feature in FEATURES)


def session_features() -> tuple[Feature, ...]:
    """The features sessions are built from: the built-ins, with what installed packages bring
    replacing or joining them (`installed.py`)."""
    return merge(FEATURES, installed())


def _named_ids(config: Any, key: str, known: Iterable[str] | None) -> set[str]:
    """The feature ids `[features] <key>` names among `known` (every session feature when None).
    A name that is no feature is logged and ignored: a typo must not stop a session, and it must
    not pass silently either."""
    ids = set(known) if known is not None else {feature.id for feature in session_features()}
    table = getattr(config, "features", None) or {}
    raw = table.get(key, []) if isinstance(table, dict) else []
    if isinstance(raw, str):
        raw = [raw]
    names = {str(name).strip() for name in raw if isinstance(name, str) and str(name).strip()} if isinstance(raw, list) else set()
    unknown = sorted(names - ids)
    if unknown:
        logger.warning("config: [features] %s names no feature: %s (known: %s)", key, ", ".join(unknown), ", ".join(sorted(ids)))
    return names & ids


def removed_ids(config: Any, known: Iterable[str] | None = None) -> set[str]:
    """The feature ids `[features] remove` names."""
    return _named_ids(config, "remove", known)


def added_ids(config: Any, known: Iterable[str] | None = None) -> set[str]:
    """The opt-in feature ids `[features] add` names. Only the person's own config can set it: the
    loader never takes `add` from a workspace (`config/loading.py`), which can only take away."""
    return _named_ids(config, "add", known)


def active_features(config: Any, features: Iterable[Feature] | None = None) -> list[Feature]:
    """`features` (the session features when None) minus the ones the config removes and the opt-in
    ones it does not add, in order. `remove` wins over `add`."""
    features = list(session_features() if features is None else features)
    known = [feature.id for feature in features]
    removed, added = removed_ids(config, known), added_ids(config, known)
    return [
        feature
        for feature in features
        if feature.id not in removed and (not getattr(feature, "opt_in", False) or feature.id in added)
    ]


__all__ = [
    "FEATURES",
    "FEATURE_IDS",
    "PROMPT_MEMORY",
    "PROMPT_RECAP",
    "Feature",
    "FeatureContext",
    "SessionInputs",
    "active_features",
    "added_ids",
    "removed_ids",
    "session_features",
]
