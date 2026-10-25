"""Features a package brings: replacing a built-in or adding one, with no edit here.

A package installed beside Reeve declares its features as entry points in the group
`reeve.features`; each entry loads a `Feature` (an instance, or a class taking no arguments).

- A feature whose `id` is a built-in's **replaces** it, in the built-in's place.
- A feature with a new `id` is **added** after the feature its `after` names. Without one, or
  naming none, it goes before `discovery`, which stays last: it defers from every tool registered
  before it.

The order is the tool list's, and the tool list is the cached prefix, so the place is decided here
and not by the order packages happen to load. Only installed code can do this: a config file names
features to remove, never code to load, so a cloned repository cannot run code by naming it. A
package that fails to load, or gives no usable feature, is logged and left out; a broken add-on
must not stop every session.
"""

from __future__ import annotations

import functools
import logging
from importlib.metadata import entry_points
from typing import Any, Iterable

from .base import Feature

logger = logging.getLogger(__name__)

GROUP = "reeve.features"
LAST = "discovery"


def _entry_points() -> Iterable[Any]:
    return entry_points(group=GROUP)


def _load(entry: Any) -> Feature | None:
    try:
        loaded = entry.load()
        feature = loaded() if isinstance(loaded, type) else loaded
    except Exception:  # an add-on's own error, whatever it is, must not stop a session
        logger.warning("feature %r (%s) failed to load; left out", getattr(entry, "name", "?"), getattr(entry, "value", "?"), exc_info=True)
        return None
    if not isinstance(getattr(feature, "id", None), str) or not feature.id or not callable(getattr(feature, "setup", None)):
        logger.warning("feature %r gives no `id` and `setup`; left out", getattr(entry, "name", "?"))
        return None
    return feature


def merge(built_in: Iterable[Feature], extra: Iterable[Feature]) -> tuple[Feature, ...]:
    """`built_in` with `extra` replacing the ones of the same id and added where they ask."""
    features = list(built_in)
    seen = {feature.id for feature in features}
    replaced: set[str] = set()
    for feature in extra:
        if feature.id in seen:
            if feature.id in replaced:
                logger.warning("feature %r is installed twice; the first one is kept", feature.id)
                continue
            index = next(i for i, current in enumerate(features) if current.id == feature.id)
            features[index] = feature
            replaced.add(feature.id)
            continue
        after = getattr(feature, "after", None)
        ids = [current.id for current in features]
        if after in ids and after != LAST:
            index = ids.index(after) + 1
        else:
            if after is not None:
                logger.warning("feature %r asks to follow %r, which is not before %r; placed before it", feature.id, after, LAST)
            index = ids.index(LAST) if LAST in ids else len(features)
        features.insert(index, feature)
        seen.add(feature.id)
        replaced.add(feature.id)
    return tuple(features)


@functools.lru_cache(maxsize=1)
def installed() -> tuple[Feature, ...]:
    """Every feature the installed packages declare, loaded once per process."""
    loaded = (_load(entry) for entry in _entry_points())
    return tuple(feature for feature in loaded if feature is not None)
