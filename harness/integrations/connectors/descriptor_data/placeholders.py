"""Connectors that are not shipped yet.

Personas recommend some of them (Ops names Datadog and PagerDuty), so the GUI draws a brand badge
and a "connect to enable" state for each. A placeholder has no fields, no `validate` and
`available=False`: `connect_connector` rejects an unavailable connector and the profile check
reports it disconnected, so there is no way to connect one by accident.
"""

from __future__ import annotations

from ..descriptor.model import ConnectorDescriptor

#: (name, title, icon, blurb, brand colour)
_ROWS = (
    ("datadog", "Datadog", "◍", "Pull firing alerts, monitors and the incident timeline.", "#632ca6"),
    ("salesforce", "Salesforce", "☁", "Read and update cases, accounts and opportunities in the CRM.", "#00a1e0"),
    ("descript", "Descript", "≣", "Read and edit audio and video projects through their transcripts.", "#0062ff"),
    ("clay", "Clay", "⌒", "Enrich people and companies and run outbound research workflows.", "#1f2328"),
    ("pagerduty", "PagerDuty", "◔", "See who is on call and review active incidents before paging.", "#06ac38"),
)


def build() -> dict[str, ConnectorDescriptor]:
    return {
        name: ConnectorDescriptor(
            name=name,
            title=title,
            icon=icon,
            blurb=blurb,
            auth="none",
            two_way=False,
            fields=[],
            instructions=[],
            available=False,
            brand_color=color,
            logo=name,
        )
        for name, title, icon, blurb, color in _ROWS
    }
