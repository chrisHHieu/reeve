"""Tools for code, issue trackers and design: GitHub, Jira, Redmine, Trello, Notion, Figma, Canva."""

from __future__ import annotations

from .types import ConnectorToolDef, row_makers

_read, _write = row_makers("github")
GITHUB: tuple[ConnectorToolDef, ...] = (
    _write("github_clone", "Clone a repo", "Clone a repository into a session folder to explore the code."),
    _write("github_pull", "Update a clone", "Fast-forward an existing clone to the latest commits."),
)

_read, _write = row_makers("jira")
JIRA: tuple[ConnectorToolDef, ...] = (
    _read("jira_search_issues", "Search issues", "Search Jira issues."),
    _read("jira_get_issue", "Read issue", "Read a Jira issue."),
    _write("jira_create_issue", "Create issue", "Create a Jira issue."),
)

_read, _write = row_makers("redmine")
REDMINE: tuple[ConnectorToolDef, ...] = (
    _read("redmine_search_issues", "Search issues", "Search Redmine issues."),
    _read("redmine_get_issue", "Read issue", "Read a Redmine issue with its comments."),
    _write("redmine_create_issue", "Create issue", "Create a Redmine issue.", target="project_id"),
    _write("redmine_update_issue", "Update issue", "Change a Redmine issue's fields or status.", target="issue_id"),
    _write("redmine_comment_issue", "Comment on issue", "Add a comment to a Redmine issue.", target="issue_id"),
    _read("redmine_list_projects", "List projects", "List Redmine projects."),
    _read("redmine_get_project", "Read project", "Read a Redmine project."),
    _read("redmine_list_time_entries", "Read time entries", "List logged time."),
    _write("redmine_log_time", "Log time", "Log time against a Redmine issue or project."),
    _read("redmine_list_enumerations", "List field ids", "List the numeric tracker, status, priority and activity ids."),
    _read("redmine_current_user", "Read my account", "Read the Redmine account this connection uses."),
)

_read, _write = row_makers("trello")
TRELLO: tuple[ConnectorToolDef, ...] = (
    _read("trello_whoami", "Read my account", "Read the Trello account this connection acts as."),
    _read("trello_list_boards", "List boards", "List the boards this account can open."),
    _read("trello_get_board", "Read board", "Read a board's lists, labels, members and custom fields."),
    _read("trello_list_cards", "List cards", "List cards on a board, in a list, or assigned to you."),
    _read("trello_get_card", "Read card", "Read a card with its checklists, attachments and comments."),
    _read("trello_search", "Search", "Search cards and boards."),
    _read("trello_activity", "Read activity", "Read what happened on a board or a card."),
    _write("trello_download_attachment", "Download attachment", "Save a card's attachment into the session folder."),
    _write("trello_create_card", "Create card", "Create or copy a card.", target="list_id"),
    _write("trello_update_card", "Update card", "Change, move, archive, label or assign a card.", target="card_id"),
    _write("trello_comment", "Comment", "Comment on a card, or edit a comment.", target="card_id"),
    _write("trello_checklist", "Checklists", "Create checklists and add, tick or change their items.", target="card_id"),
    _write("trello_attach", "Attach", "Attach a file from the session folder or a link to a card.", target="card_id"),
    _write("trello_set_custom_field", "Set custom field", "Set or clear a card's custom field.", target="card_id"),
    _write("trello_list", "Lists", "Create, rename, move or archive lists and their cards.", target="board_id"),
    _write("trello_label", "Labels", "Create or change a board's labels.", target="board_id"),
    _write("trello_board", "Boards", "Create, rename or archive a board."),
    _write("trello_delete", "Delete", "Delete a card, comment, checklist, item, attachment or label for good."),
)

_read, _write = row_makers("notion")
NOTION: tuple[ConnectorToolDef, ...] = (
    _read("notion_search", "Search", "Search Notion pages and databases."),
    _read("notion_read_page", "Read page", "Read a page's properties and content."),
    _read("notion_query_database", "Query database", "Query a Notion database."),
    _write("notion_create_page", "Create page", "Create a page under a parent page."),
)

_read, _write = row_makers("figma")
FIGMA: tuple[ConnectorToolDef, ...] = (
    _read("figma_get_file", "Read file", "Read a file's pages and frames."),
    _read("figma_get_comments", "List comments", "List comments on a file."),
    _write("figma_post_comment", "Comment", "Comment on a file.", target="file_key"),
    _read("figma_export_images", "Export images", "Render nodes to image URLs."),
)

_read, _write = row_makers("canva")
CANVA: tuple[ConnectorToolDef, ...] = (
    _read("canva_list_designs", "List designs", "List or search designs."),
    _read("canva_get_design", "Read design", "Read a design's metadata."),
    _read("canva_export_design", "Export design", "Start rendering a design to pdf/png/jpg."),
    _read("canva_get_export", "Check export", "Poll an export job for download URLs."),
)

ALL: tuple[ConnectorToolDef, ...] = GITHUB + JIRA + REDMINE + TRELLO + NOTION + FIGMA + CANVA
