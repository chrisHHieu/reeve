"""Connectors a person may use at their own risk; release builds do not contain them.

They show only with the experimental-connectors setting on, each asks the person to acknowledge
its risk before connecting, and `packaging/reeve-server.spec` leaves the package out of the
official desktop builds (`REEVE_EXPERIMENTAL=1` when building puts it in a build of your own).

Adding one: a `ConnectorDescriptor` whose `risk_notice` says in plain words what can go wrong,
appended to `EXPERIMENTAL_DESCRIPTORS`, with its tools or adapter registered as any other
connector's are. `descriptors.py` marks each one `experimental`, whatever the descriptor says.
"""

from __future__ import annotations

from ..descriptors import ConnectorDescriptor

# Empty for now. WhatsApp (personal) graduated on 2026-10-21 at the owner's word: it is a regular
# connector (descriptor_data/messaging.py) that still says its risk and asks for the person's
# acknowledgment before a phone is linked.
EXPERIMENTAL_DESCRIPTORS: list[ConnectorDescriptor] = []
