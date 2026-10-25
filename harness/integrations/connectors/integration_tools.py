"""Assembles the first-party connector tools.

Each connector's builder is in `tool_builders/`; what they share is `calls` (the HTTP call),
`headers`, `profiles` (stored credentials) and `toolkit` (attaching a schema to a function).
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from harness.safety.secrets import SecretStore
from .mail import make_email_tools
from .catalog import connector_for_tool
from .tool_builders import (
    github_tools,
    gmail_tools,
    gcal_tools,
    outlook_tools,
    outlook_mail_tools,
    onedrive_tools,
    teams_tools,
    jira_tools,
    redmine_tools,
    trello_tools,
    trello_card_tools,
    trello_board_tools,
    whatsapp_tools,
    notion_tools,
    figma_tools,
    drive_tools,
    google_workspace_tools,
    gmail_labels_tools,
    gmail_mail_tools,
    gmail_bulk_tools,
    slack_tools,
)


def make_integration_tools(
    secrets: SecretStore,
    *,
    enabled_connectors: Optional[set[str]] = None,
    enabled_tools: Optional[set[str]] = None,
    roots: Optional[list[Any]] = None,
    remote: bool = False,
) -> list[Callable[..., Any]]:
    tools: list[Callable[..., Any]] = []
    tools.extend(make_email_tools(secrets, roots=roots, remote=remote))
    tools.extend(github_tools(secrets, roots=roots, remote=remote))
    tools.extend(gmail_tools(secrets, roots=roots, remote=remote))
    tools.extend(gcal_tools(secrets, roots=roots))
    tools.extend(outlook_tools(secrets, roots=roots))
    tools.extend(outlook_mail_tools(secrets, roots=roots, remote=remote))
    tools.extend(onedrive_tools(secrets, roots=roots, remote=remote))
    tools.extend(teams_tools(secrets, roots=roots, remote=remote))
    tools.extend(jira_tools(secrets, roots=roots))
    tools.extend(redmine_tools(secrets, roots=roots))
    tools.extend(trello_tools(secrets, roots=roots, remote=remote))
    tools.extend(trello_card_tools(secrets, roots=roots, remote=remote))
    tools.extend(trello_board_tools(secrets, roots=roots, remote=remote))
    tools.extend(whatsapp_tools(secrets, roots=roots))
    tools.extend(notion_tools(secrets, roots=roots))
    tools.extend(figma_tools(secrets, roots=roots))
    tools.extend(drive_tools(secrets, roots=roots))
    tools.extend(google_workspace_tools(secrets, roots=roots))
    tools.extend(gmail_labels_tools(secrets, roots=roots, remote=remote))
    tools.extend(gmail_mail_tools(secrets, roots=roots, remote=remote))
    tools.extend(gmail_bulk_tools(secrets))
    tools.extend(slack_tools(secrets, roots=roots, remote=remote))

    def wanted(tool: Callable[..., Any]) -> bool:
        name = tool.__name__
        if enabled_connectors is not None and connector_for_tool(name) not in enabled_connectors:
            return False
        return enabled_tools is None or name in enabled_tools

    tools = [tool for tool in tools if wanted(tool)]
    # Every connector tool is situational (amendment 5): a session that never touches the
    # mailbox still carried Gmail's 36 schemas on every request, about 18k characters, and 3 of the
    # 36 sessions that carried them called one. They sit behind `tool_search`, listed under their
    # connector, and the first use loads one.
    for tool in tools:
        tool.__tool_defer__ = True
        tool.__tool_catalog__ = connector_for_tool(tool.__name__) or "connectors"
    return tools
