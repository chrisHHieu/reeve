"""Integration tools: google_workspace (tách từ integration_tools.py, hành vi giữ nguyên)."""
from __future__ import annotations

from .. import calls
from ..toolkit import attach, function_schema, clamp
from ..profiles import default_profile, account_profile, gcal_profile, gcal_result, GENERIC_ACCOUNT_PROP, CALENDAR_ACCOUNT_PROP
from ..headers import google_headers

from typing import Any, Callable, Optional


def google_workspace_tools(secrets: Any, *, roots: Optional[list[Any]] = None) -> list[Callable[..., Any]]:
    tools: list[Callable[..., Any]] = []
    _GAPI = "https://www.googleapis.com"

    def _json_arg(raw: Any, field: str) -> tuple[Any, Optional[dict[str, str]]]:
        """Parse a JSON-shaped argument. The model supplies these as strings."""
        if raw in ("", None):
            return None, {"error": f"{field} is required"}
        if isinstance(raw, (list, dict)):
            return raw, None
        import json as _json

        try:
            return _json.loads(raw), None
        except Exception:
            return None, {"error": f"{field} must be valid JSON"}

    def _google_profile(connector: str) -> tuple[Optional[dict[str, Any]], Optional[dict[str, str]]]:
        """Single-profile Google connectors (docs / sheets / slides)."""
        return default_profile(secrets, connector, "access_token")

    # -- Calendar ----------------------------------------------------------------------

    def gcal_get_event(
        event_id: str, calendar_id: str = "primary", account: str = ""
    ) -> dict[str, Any]:
        email, profile, err = gcal_profile(secrets, account)
        if err:
            return err
        return gcal_result(
            email,
            calls.request(
                "GET",
                f"{_GAPI}/calendar/v3/calendars/{calendar_id}/events/{event_id}",
                headers=google_headers(profile["access_token"]),
            ),
        )

    gcal_get_event.__name__ = "gcal_get_event"
    tools.append(
        attach(
            gcal_get_event,
            function_schema(
                "gcal_get_event",
                "Read one Google Calendar event by its id.",
                {
                    "event_id": {"type": "string"},
                    "calendar_id": {"type": "string"},
                    "account": CALENDAR_ACCOUNT_PROP,
                },
                ["event_id"],
            ),
            caps=["calendar", "read"],
        )
    )

    def gcal_list_calendars(account: str = "") -> dict[str, Any]:
        email, profile, err = gcal_profile(secrets, account)
        if err:
            return err
        return gcal_result(
            email,
            calls.request(
                "GET",
                f"{_GAPI}/calendar/v3/users/me/calendarList",
                headers=google_headers(profile["access_token"]),
                params={"maxResults": 50},
            ),
        )

    gcal_list_calendars.__name__ = "gcal_list_calendars"
    tools.append(
        attach(
            gcal_list_calendars,
            function_schema(
                "gcal_list_calendars",
                "List the calendars available on this Google account.",
                {"account": CALENDAR_ACCOUNT_PROP},
                [],
            ),
            caps=["calendar", "read"],
        )
    )

    def gcal_search_events(
        query: str,
        calendar_id: str = "primary",
        max_results: int = 10,
        account: str = "",
    ) -> dict[str, Any]:
        email, profile, err = gcal_profile(secrets, account)
        if err:
            return err
        return gcal_result(
            email,
            calls.request(
                "GET",
                f"{_GAPI}/calendar/v3/calendars/{calendar_id}/events",
                headers=google_headers(profile["access_token"]),
                params={
                    "q": query,
                    "singleEvents": True,
                    "orderBy": "startTime",
                    "maxResults": clamp(max_results),
                },
            ),
        )

    gcal_search_events.__name__ = "gcal_search_events"
    tools.append(
        attach(
            gcal_search_events,
            function_schema(
                "gcal_search_events",
                "Search Google Calendar events by free text.",
                {
                    "query": {"type": "string"},
                    "calendar_id": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": CALENDAR_ACCOUNT_PROP,
                },
                ["query"],
            ),
            caps=["calendar", "read"],
        )
    )

    _RSVP = {"accepted", "declined", "tentative"}

    def gcal_respond_to_event(
        event_id: str,
        response: str,
        calendar_id: str = "primary",
        account: str = "",
    ) -> dict[str, Any]:
        email, profile, err = gcal_profile(secrets, account)
        if err:
            return err
        answer = (response or "").strip().lower()
        if answer not in _RSVP:
            return {"error": f"response must be one of {sorted(_RSVP)}"}
        headers = google_headers(profile["access_token"])
        url = f"{_GAPI}/calendar/v3/calendars/{calendar_id}/events/{event_id}"
        current = calls.request("GET", url, headers=headers)
        if current.get("error"):
            return current
        # Google has no RSVP endpoint: you PATCH the event with the attendee list, and the
        # only attendee you may change is your own (`self: true`). Sending the whole list
        # back untouched apart from that one field is what keeps the other guests intact.
        attendees = (current.get("data") or current).get("attendees") or []
        mine = [a for a in attendees if a.get("self")]
        if not mine:
            return {"error": "you are not an attendee of this event"}
        for attendee in mine:
            attendee["responseStatus"] = answer
        return gcal_result(
            email,
            calls.request("PATCH", url, headers=headers, json={"attendees": attendees}),
        )

    gcal_respond_to_event.__name__ = "gcal_respond_to_event"
    tools.append(
        attach(
            gcal_respond_to_event,
            function_schema(
                "gcal_respond_to_event",
                "Respond to a calendar invitation: accepted, declined, or tentative.",
                {
                    "event_id": {"type": "string"},
                    "response": {
                        "type": "string",
                        "enum": ["accepted", "declined", "tentative"],
                    },
                    "calendar_id": {"type": "string"},
                    "account": CALENDAR_ACCOUNT_PROP,
                },
                ["event_id", "response"],
            ),
            approval=True,
            caps=["calendar", "write"],
        )
    )

    # -- Drive -------------------------------------------------------------------------

    # NOT `_DRIVE_FIELDS`: that name is already bound earlier in this same function to
    # the files.LIST shape, `files(...)`. Both tool groups live in one closure, so a
    # second assignment does not shadow locally — it REBINDS the one variable, and every
    # earlier tool then sends the wrong shape at call time. Drive answered
    # "Invalid field selection id" for drive_search_files and drive_list_folder while the
    # newer tools worked, which is why no test noticed.
    _DRIVE_ITEM_FIELDS = (
        "id,name,mimeType,size,modifiedTime,"
        "owners(displayName,emailAddress),webViewLink"
    )

    def drive_file_metadata(file_id: str, account: str = "") -> dict[str, Any]:
        aid, profile, err = account_profile(
            secrets, "google_drive", account, "access_token"
        )
        if err:
            return err
        return calls.request(
            "GET",
            f"{_GAPI}/drive/v3/files/{file_id}",
            headers=google_headers(profile["access_token"]),
            params={"fields": _DRIVE_ITEM_FIELDS, "supportsAllDrives": True},
        )

    drive_file_metadata.__name__ = "drive_file_metadata"
    tools.append(
        attach(
            drive_file_metadata,
            function_schema(
                "drive_file_metadata",
                "Name, type, size, owners and timestamps for one Drive file.",
                {"file_id": {"type": "string"}, "account": GENERIC_ACCOUNT_PROP},
                ["file_id"],
            ),
            caps=["drive", "read"],
        )
    )

    def drive_recent_files(max_results: int = 10, account: str = "") -> dict[str, Any]:
        aid, profile, err = account_profile(
            secrets, "google_drive", account, "access_token"
        )
        if err:
            return err
        return calls.request(
            "GET",
            f"{_GAPI}/drive/v3/files",
            headers=google_headers(profile["access_token"]),
            params={
                "orderBy": "modifiedTime desc",
                "pageSize": clamp(max_results),
                "fields": f"files({_DRIVE_ITEM_FIELDS})",
            },
        )

    drive_recent_files.__name__ = "drive_recent_files"
    tools.append(
        attach(
            drive_recent_files,
            function_schema(
                "drive_recent_files",
                "List recently modified Drive files.",
                {"max_results": {"type": "integer"}, "account": GENERIC_ACCOUNT_PROP},
                [],
            ),
            caps=["drive", "read"],
        )
    )

    def drive_file_permissions(file_id: str, account: str = "") -> dict[str, Any]:
        aid, profile, err = account_profile(
            secrets, "google_drive", account, "access_token"
        )
        if err:
            return err
        return calls.request(
            "GET",
            f"{_GAPI}/drive/v3/files/{file_id}/permissions",
            headers=google_headers(profile["access_token"]),
            params={
                "fields": "permissions(id,type,role,emailAddress,displayName)",
                "supportsAllDrives": True,
            },
        )

    drive_file_permissions.__name__ = "drive_file_permissions"
    tools.append(
        attach(
            drive_file_permissions,
            function_schema(
                "drive_file_permissions",
                "Who a Drive file is shared with, and at what role.",
                {"file_id": {"type": "string"}, "account": GENERIC_ACCOUNT_PROP},
                ["file_id"],
            ),
            caps=["drive", "read"],
        )
    )

    def drive_create_file(
        name: str,
        content: str = "",
        mime_type: str = "text/plain",
        folder_id: str = "",
        account: str = "",
    ) -> dict[str, Any]:
        aid, profile, err = account_profile(
            secrets, "google_drive", account, "access_token"
        )
        if err:
            return err
        metadata: dict[str, Any] = {"name": name, "mimeType": mime_type}
        if folder_id:
            metadata["parents"] = [folder_id]
        created = calls.request(
            "POST",
            f"{_GAPI}/drive/v3/files",
            headers=google_headers(profile["access_token"]),
            json=metadata,
            params={"fields": _DRIVE_ITEM_FIELDS},
        )
        if created.get("error") or not content:
            return created
        file_id = ((created.get("data") or created) or {}).get("id")
        if not file_id:
            return created
        # Metadata and bytes are two calls on the simple (non-multipart) path. Upload
        # failing after create leaves an empty file rather than nothing, so say so.
        import httpx

        try:
            with httpx.Client(timeout=30.0) as client:
                up = client.patch(
                    f"https://www.googleapis.com/upload/drive/v3/files/{file_id}",
                    params={"uploadType": "media"},
                    headers={
                        "Authorization": f"Bearer {profile['access_token']}",
                        "Content-Type": mime_type,
                    },
                    content=content.encode("utf-8"),
                )
            if up.status_code >= 400:
                return {
                    "error": f"file created but content upload failed: HTTP {up.status_code}",
                    "file_id": file_id,
                }
        except Exception as exc:
            return {"error": f"file created but content upload failed: {exc}", "file_id": file_id}
        return created

    drive_create_file.__name__ = "drive_create_file"
    tools.append(
        attach(
            drive_create_file,
            function_schema(
                "drive_create_file",
                "Create a Drive file with optional text content.",
                {
                    "name": {"type": "string"},
                    "content": {"type": "string"},
                    "mime_type": {"type": "string"},
                    "folder_id": {"type": "string"},
                    "account": GENERIC_ACCOUNT_PROP,
                },
                ["name"],
            ),
            approval=True,
            caps=["drive", "write"],
        )
    )

    def drive_copy_file(
        file_id: str, name: str = "", account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = account_profile(
            secrets, "google_drive", account, "access_token"
        )
        if err:
            return err
        body = {"name": name} if name else {}
        return calls.request(
            "POST",
            f"{_GAPI}/drive/v3/files/{file_id}/copy",
            headers=google_headers(profile["access_token"]),
            json=body,
            params={"fields": _DRIVE_ITEM_FIELDS, "supportsAllDrives": True},
        )

    drive_copy_file.__name__ = "drive_copy_file"
    tools.append(
        attach(
            drive_copy_file,
            function_schema(
                "drive_copy_file",
                "Copy a Drive file, optionally naming the copy.",
                {
                    "file_id": {"type": "string"},
                    "name": {"type": "string"},
                    "account": GENERIC_ACCOUNT_PROP,
                },
                ["file_id"],
            ),
            approval=True,
            caps=["drive", "write"],
        )
    )

    # -- Docs --------------------------------------------------------------------------

    def docs_read_document(document_id: str) -> dict[str, Any]:
        profile, err = _google_profile("google_docs")
        if err:
            return err
        return calls.request(
            "GET",
            f"https://docs.googleapis.com/v1/documents/{document_id}",
            headers=google_headers(profile["access_token"]),
        )

    docs_read_document.__name__ = "docs_read_document"
    tools.append(
        attach(
            docs_read_document,
            function_schema(
                "docs_read_document",
                "Read a Google Doc: its text content and structure.",
                {"document_id": {"type": "string"}},
                ["document_id"],
            ),
            caps=["docs", "read"],
        )
    )

    def docs_update_document(document_id: str, requests: Any = "") -> dict[str, Any]:
        profile, err = _google_profile("google_docs")
        if err:
            return err
        parsed, jerr = _json_arg(requests, "requests")
        if jerr:
            return jerr
        if not isinstance(parsed, list):
            return {"error": "requests must be a JSON array of Docs API requests"}
        return calls.request(
            "POST",
            f"https://docs.googleapis.com/v1/documents/{document_id}:batchUpdate",
            headers=google_headers(profile["access_token"]),
            json={"requests": parsed},
        )

    docs_update_document.__name__ = "docs_update_document"
    tools.append(
        attach(
            docs_update_document,
            function_schema(
                "docs_update_document",
                "Edit a Google Doc by applying Docs API batchUpdate requests "
                '(a JSON array, e.g. [{"insertText": {"location": {"index": 1}, '
                '"text": "hello"}}]).',
                {
                    "document_id": {"type": "string"},
                    "requests": {"type": "string"},
                },
                ["document_id", "requests"],
            ),
            approval=True,
            caps=["docs", "write"],
        )
    )

    # -- Sheets ------------------------------------------------------------------------

    _SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"

    def sheets_get_values(spreadsheet_id: str, range: str = "A1:Z100") -> dict[str, Any]:
        profile, err = _google_profile("google_sheets")
        if err:
            return err
        return calls.request(
            "GET",
            f"{_SHEETS}/{spreadsheet_id}/values/{range}",
            headers=google_headers(profile["access_token"]),
        )

    sheets_get_values.__name__ = "sheets_get_values"
    tools.append(
        attach(
            sheets_get_values,
            function_schema(
                "sheets_get_values",
                "Read a range of cells, in A1 notation (e.g. 'Sheet1!A1:D20').",
                {"spreadsheet_id": {"type": "string"}, "range": {"type": "string"}},
                ["spreadsheet_id"],
            ),
            caps=["sheets", "read"],
        )
    )

    def sheets_get_spreadsheet(spreadsheet_id: str) -> dict[str, Any]:
        profile, err = _google_profile("google_sheets")
        if err:
            return err
        return calls.request(
            "GET",
            f"{_SHEETS}/{spreadsheet_id}",
            headers=google_headers(profile["access_token"]),
            # Grid data would be the whole workbook; the structure is what a caller needs
            # in order to ask for the right range next.
            params={
                "fields": "spreadsheetId,properties.title,"
                "sheets(properties(sheetId,title,index,gridProperties))"
            },
        )

    sheets_get_spreadsheet.__name__ = "sheets_get_spreadsheet"
    tools.append(
        attach(
            sheets_get_spreadsheet,
            function_schema(
                "sheets_get_spreadsheet",
                "Sheet names, ids and grid sizes for a spreadsheet.",
                {"spreadsheet_id": {"type": "string"}},
                ["spreadsheet_id"],
            ),
            caps=["sheets", "read"],
        )
    )

    def _sheets_write(
        spreadsheet_id: str, range: str, values: Any, how: str
    ) -> dict[str, Any]:
        profile, err = _google_profile("google_sheets")
        if err:
            return err
        parsed, jerr = _json_arg(values, "values")
        if jerr:
            return jerr
        if not isinstance(parsed, list):
            return {"error": "values must be a JSON array of rows, e.g. [[1,2],[3,4]]"}
        rows = [r if isinstance(r, list) else [r] for r in parsed]
        return calls.request(
            "PUT",
            f"{_SHEETS}/{spreadsheet_id}/values/{range}",
            headers=google_headers(profile["access_token"]),
            params={"valueInputOption": how},
            json={"values": rows},
        )

    def sheets_update_values(
        spreadsheet_id: str, range: str, values: Any = ""
    ) -> dict[str, Any]:
        # RAW: what is sent is what is stored. A leading "=" stays literal text.
        return _sheets_write(spreadsheet_id, range, values, "RAW")

    sheets_update_values.__name__ = "sheets_update_values"
    tools.append(
        attach(
            sheets_update_values,
            function_schema(
                "sheets_update_values",
                "Write literal values into a range. values is a JSON array of rows, "
                'e.g. [["Name","Total"],["Acme",120]].',
                {
                    "spreadsheet_id": {"type": "string"},
                    "range": {"type": "string"},
                    "values": {"type": "string"},
                },
                ["spreadsheet_id", "range", "values"],
            ),
            approval=True,
            caps=["sheets", "write"],
        )
    )

    def sheets_update_formulas(
        spreadsheet_id: str, range: str, values: Any = ""
    ) -> dict[str, Any]:
        # USER_ENTERED parses the strings the way typing them would: "=SUM(A1:A9)"
        # becomes a formula, "5%" becomes a number. That is the whole difference.
        return _sheets_write(spreadsheet_id, range, values, "USER_ENTERED")

    sheets_update_formulas.__name__ = "sheets_update_formulas"
    tools.append(
        attach(
            sheets_update_formulas,
            function_schema(
                "sheets_update_formulas",
                "Write formulas into a range, parsed as if typed. values is a JSON array "
                'of rows, e.g. [["=SUM(B2:B9)"]].',
                {
                    "spreadsheet_id": {"type": "string"},
                    "range": {"type": "string"},
                    "values": {"type": "string"},
                },
                ["spreadsheet_id", "range", "values"],
            ),
            approval=True,
            caps=["sheets", "write"],
        )
    )

    def sheets_insert_dimension(
        spreadsheet_id: str,
        sheet_id: int = 0,
        dimension: str = "ROWS",
        start_index: int = 0,
        end_index: int = 1,
    ) -> dict[str, Any]:
        profile, err = _google_profile("google_sheets")
        if err:
            return err
        which = (dimension or "ROWS").strip().upper()
        if which not in ("ROWS", "COLUMNS"):
            return {"error": "dimension must be ROWS or COLUMNS"}
        if int(end_index) <= int(start_index):
            return {"error": "end_index must be greater than start_index"}
        return calls.request(
            "POST",
            f"{_SHEETS}/{spreadsheet_id}:batchUpdate",
            headers=google_headers(profile["access_token"]),
            json={
                "requests": [
                    {
                        "insertDimension": {
                            "range": {
                                "sheetId": int(sheet_id),
                                "dimension": which,
                                "startIndex": int(start_index),
                                "endIndex": int(end_index),
                            }
                        }
                    }
                ]
            },
        )

    sheets_insert_dimension.__name__ = "sheets_insert_dimension"
    tools.append(
        attach(
            sheets_insert_dimension,
            function_schema(
                "sheets_insert_dimension",
                "Insert rows or columns. Indexes are 0-based and end_index is exclusive; "
                "sheet_id comes from sheets_get_spreadsheet.",
                {
                    "spreadsheet_id": {"type": "string"},
                    "sheet_id": {"type": "integer"},
                    "dimension": {"type": "string", "enum": ["ROWS", "COLUMNS"]},
                    "start_index": {"type": "integer"},
                    "end_index": {"type": "integer"},
                },
                ["spreadsheet_id"],
            ),
            approval=True,
            caps=["sheets", "write"],
        )
    )

    def sheets_update_spreadsheet(
        spreadsheet_id: str, requests: Any = ""
    ) -> dict[str, Any]:
        profile, err = _google_profile("google_sheets")
        if err:
            return err
        parsed, jerr = _json_arg(requests, "requests")
        if jerr:
            return jerr
        if not isinstance(parsed, list):
            return {"error": "requests must be a JSON array of Sheets API requests"}
        return calls.request(
            "POST",
            f"{_SHEETS}/{spreadsheet_id}:batchUpdate",
            headers=google_headers(profile["access_token"]),
            json={"requests": parsed},
        )

    sheets_update_spreadsheet.__name__ = "sheets_update_spreadsheet"
    tools.append(
        attach(
            sheets_update_spreadsheet,
            function_schema(
                "sheets_update_spreadsheet",
                "Change spreadsheet structure by applying Sheets API batchUpdate "
                "requests (a JSON array): add sheets, format cells, sort, and so on.",
                {
                    "spreadsheet_id": {"type": "string"},
                    "requests": {"type": "string"},
                },
                ["spreadsheet_id", "requests"],
            ),
            approval=True,
            caps=["sheets", "write"],
        )
    )

    # -- Slides ------------------------------------------------------------------------

    _SLIDES = "https://slides.googleapis.com/v1/presentations"

    def slides_read_presentation(presentation_id: str) -> dict[str, Any]:
        profile, err = _google_profile("google_slides")
        if err:
            return err
        return calls.request(
            "GET",
            f"{_SLIDES}/{presentation_id}",
            headers=google_headers(profile["access_token"]),
        )

    slides_read_presentation.__name__ = "slides_read_presentation"
    tools.append(
        attach(
            slides_read_presentation,
            function_schema(
                "slides_read_presentation",
                "Read a presentation: its slides, layouts and text.",
                {"presentation_id": {"type": "string"}},
                ["presentation_id"],
            ),
            caps=["slides", "read"],
        )
    )

    def slides_update_presentation(
        presentation_id: str, requests: Any = ""
    ) -> dict[str, Any]:
        profile, err = _google_profile("google_slides")
        if err:
            return err
        parsed, jerr = _json_arg(requests, "requests")
        if jerr:
            return jerr
        if not isinstance(parsed, list):
            return {"error": "requests must be a JSON array of Slides API requests"}
        return calls.request(
            "POST",
            f"{_SLIDES}/{presentation_id}:batchUpdate",
            headers=google_headers(profile["access_token"]),
            json={"requests": parsed},
        )

    slides_update_presentation.__name__ = "slides_update_presentation"
    tools.append(
        attach(
            slides_update_presentation,
            function_schema(
                "slides_update_presentation",
                "Edit a presentation by applying Slides API batchUpdate requests "
                "(a JSON array): create slides, insert or replace text, move elements.",
                {
                    "presentation_id": {"type": "string"},
                    "requests": {"type": "string"},
                },
                ["presentation_id", "requests"],
            ),
            approval=True,
            caps=["slides", "write"],
        )
    )


    # ---- Gmail, mirroring Google's own MCP server tool for tool ------------------------
    # That server exists and serves these 23 to an anonymous caller, but refuses every
    # `tools/call` outside the Workspace Developer Preview Program. The Gmail API behind
    # it does not, so these are ours. `gmail_send_email` above has no MCP counterpart —
    # Google stops at drafts — and is kept because dropping a shipped capability to match
    # someone else's catalogue is a regression, not fidelity.
    return tools
