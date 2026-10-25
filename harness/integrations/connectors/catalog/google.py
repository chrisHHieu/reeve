"""Google Workspace tools other than Gmail: Calendar, Drive, Docs, Sheets, Slides."""

from __future__ import annotations

from .types import ConnectorToolDef, row_makers

_read, _write = row_makers("google_calendar")
GOOGLE_CALENDAR: tuple[ConnectorToolDef, ...] = (
    _read("gcal_list_events", "List events", "List Google Calendar events."),
    _read("gcal_free_busy", "Check availability", "Look up busy intervals across calendars."),
    _write("gcal_create_event", "Create event", "Create a Google Calendar event."),
    _write("gcal_update_event", "Update event", "Change fields of an existing event."),
    _write("gcal_delete_event", "Delete event", "Delete a calendar event."),
    _read("gcal_get_event", "Get event", "Read one calendar event by id."),
    _read("gcal_list_calendars", "List calendars", "List the calendars on this account."),
    _read("gcal_search_events", "Search events", "Search events by free text."),
    _write("gcal_respond_to_event", "Respond to invite", "Accept, decline, or tentatively accept an invitation."),
)

_read, _write = row_makers("google_drive")
GOOGLE_DRIVE: tuple[ConnectorToolDef, ...] = (
    _read("drive_file_metadata", "File details", "Name, type, size, owners and timestamps for one file."),
    _read("drive_recent_files", "Recent files", "List recently modified files."),
    _read("drive_file_permissions", "File sharing", "Who a file is shared with."),
    _write("drive_create_file", "Create file", "Create a text file in Drive."),
    _write("drive_copy_file", "Copy file", "Copy an existing Drive file."),
    _read("drive_search_files", "Search files", "Search Drive files by name or content."),
    _read("drive_list_folder", "List folder", "List a Drive folder's contents."),
    _read("drive_read_file", "Read file", "Read a Drive file as text."),
)

_read, _write = row_makers("google_docs")
GOOGLE_DOCS: tuple[ConnectorToolDef, ...] = (
    _read("docs_read_document", "Read document", "Read a Google Doc's text and structure."),
    _write("docs_update_document", "Edit document", "Apply Docs API batchUpdate requests to a document."),
)

_read, _write = row_makers("google_sheets")
GOOGLE_SHEETS: tuple[ConnectorToolDef, ...] = (
    _read("sheets_get_values", "Read cells", "Read a range of cell values."),
    _read("sheets_get_spreadsheet", "Spreadsheet info", "Sheet names, ids, and grid sizes."),
    _write("sheets_update_values", "Write cells", "Write literal values into a range."),
    _write("sheets_update_formulas", "Write formulas", "Write formulas into a range (entered as a user would type them)."),
    _write("sheets_insert_dimension", "Insert rows/columns", "Insert rows or columns into a sheet."),
    _write("sheets_update_spreadsheet", "Edit spreadsheet", "Apply Sheets API batchUpdate requests to a spreadsheet."),
)

_read, _write = row_makers("google_slides")
GOOGLE_SLIDES: tuple[ConnectorToolDef, ...] = (
    _read("slides_read_presentation", "Read presentation", "Read a deck's slides and text."),
    _write("slides_update_presentation", "Edit presentation", "Apply Slides API batchUpdate requests to a presentation."),
)

ALL: tuple[ConnectorToolDef, ...] = GOOGLE_CALENDAR + GOOGLE_DRIVE + GOOGLE_DOCS + GOOGLE_SHEETS + GOOGLE_SLIDES
