"""One tool in the connector catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class ConnectorToolDef:
    connector: str
    name: str
    label: str
    #: `read` never asks for approval; `write` always does.
    kind: str
    description: str
    default_enabled: bool = True
    #: The argument that names the external object the tool acts on (a channel, a recipient).
    #: Declaring one makes the tool eligible for a task-scoped standing rule: "this automation
    #: may call this tool against this exact target without asking". Only single-argument
    #: targets exist (no wildcards, no composite targets), and only write tools declare one,
    #: because a rule on a read would mean nothing: reads never ask.
    target_arg: Optional[str] = None
    #: How the connector page groups a long catalogue (`read`, `write`, `admin`); empty is flat.
    group: str = ""


def row_makers(connector: str) -> tuple[Callable[..., ConnectorToolDef], Callable[..., ConnectorToolDef]]:
    """(read, write) constructors for a connector's rows: `read(name, label, description)` and
    `write(name, label, description, target=None)`."""

    def read(name: str, label: str, description: str) -> ConnectorToolDef:
        return ConnectorToolDef(connector, name, label, "read", description)

    def write(name: str, label: str, description: str, target: Optional[str] = None) -> ConnectorToolDef:
        return ConnectorToolDef(connector, name, label, "write", description, target_arg=target)

    return read, write
