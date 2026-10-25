"""The connectors Reeve knows, in the order the Connectors page lists them.

They are built by family (`messaging`, `google`, `microsoft`, `vendors`, `placeholders`) and put in one order
here. The Google family reads its client ids when it is built, so reloading this package after a
change of environment gives the new values.
"""

from __future__ import annotations

from ..descriptor.model import ConnectorDescriptor
from . import google, messaging, microsoft, placeholders, vendors

_ORDER = (
    "telegram",
    "slack",
    "email",
    "gmail",
    "google_calendar",
    "github",
    "outlook",
    "onedrive",
    "teams",
    "jira",
    "tcbs",
    "firecrawl",
    "redmine",
    "trello",
    "postman",
    "upwork",
    "whatsapp",
    "whatsapp_personal",
    "google_docs",
    "google_sheets",
    "google_slides",
    "datadog",
    "salesforce",
    "google_drive",
    "canva",
    "figma",
    "descript",
    "clay",
    "notion",
    "pagerduty",
)


def _assemble() -> list[ConnectorDescriptor]:
    built = {d.name: d for d in [*messaging.build(), *google.build(), *microsoft.build(), *vendors.build()]}
    built.update(placeholders.build())
    missing = [name for name in _ORDER if name not in built]
    extra = [name for name in built if name not in _ORDER]
    if missing or extra:
        raise RuntimeError(f"connector order is out of step: missing {missing}, unlisted {extra}")
    return [built[name] for name in _ORDER]


DESCRIPTORS: list[ConnectorDescriptor] = _assemble()
