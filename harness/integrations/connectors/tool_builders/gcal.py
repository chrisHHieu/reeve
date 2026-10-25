"""Google Calendar tools: list, free/busy, create, update and delete events."""

from __future__ import annotations

from typing import Any, Callable, Optional

from .. import calls
from ..headers import google_headers
from ..profiles import CALENDAR_ACCOUNT_PROP, gcal_profile, gcal_result
from ..toolkit import attach, function_schema

_API = "https://www.googleapis.com/calendar/v3"
_DEFAULT_PAGE = 10
_MAX_PAGE = 20


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """The tool schema, with the `account` choice every calendar tool shares."""
    return function_schema(name, description, {**properties, "account": CALENDAR_ACCOUNT_PROP}, required)


def _moment(when: str, timezone: str) -> dict[str, str]:
    return {"dateTime": when, "timeZone": timezone}


def gcal_tools(secrets: Any, *, roots: Optional[list[Any]] = None) -> list[Callable[..., Any]]:
    def send(account: str, method: str, url: str, **options: Any):
        """Resolve the account, then make the call with its token. Returns the failure first."""
        email, profile, failure = gcal_profile(secrets, account)
        if failure:
            return None, failure
        headers = google_headers(profile["access_token"])
        return email, calls.request(method, url, headers=headers, **options)

    def done(sent: tuple) -> dict[str, Any]:
        email, outcome = sent
        return outcome if email is None else gcal_result(email, outcome)

    def gcal_list_events(
        calendar_id: str = "primary",
        time_min: str = "",
        time_max: str = "",
        max_results: int = _DEFAULT_PAGE,
        account: str = "",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": max(1, min(int(max_results or _DEFAULT_PAGE), _MAX_PAGE)),
        }
        if time_min:
            params["timeMin"] = time_min
        if time_max:
            params["timeMax"] = time_max
        return done(send(account, "GET", f"{_API}/calendars/{calendar_id}/events", params=params))

    def gcal_free_busy(
        time_min: str, time_max: str, calendars: str = "primary", timezone: str = "UTC", account: str = ""
    ) -> dict[str, Any]:
        items = [{"id": name.strip()} for name in str(calendars or "primary").split(",") if name.strip()]
        body = {"timeMin": time_min, "timeMax": time_max, "timeZone": timezone, "items": items}
        return done(send(account, "POST", f"{_API}/freeBusy", json=body))

    def gcal_create_event(
        summary: str,
        start: str,
        end: str,
        calendar_id: str = "primary",
        timezone: str = "UTC",
        description: str = "",
        account: str = "",
    ) -> dict[str, Any]:
        body = {
            "summary": summary,
            "description": description,
            "start": _moment(start, timezone),
            "end": _moment(end, timezone),
        }
        return done(send(account, "POST", f"{_API}/calendars/{calendar_id}/events", json=body))

    def gcal_update_event(
        event_id: str,
        calendar_id: str = "primary",
        summary: str = "",
        start: str = "",
        end: str = "",
        timezone: str = "UTC",
        description: str = "",
        account: str = "",
    ) -> dict[str, Any]:
        email, profile, failure = gcal_profile(secrets, account)
        if failure:
            return failure
        # PATCH semantics: only the fields that were given change.
        body: dict[str, Any] = {}
        if summary:
            body["summary"] = summary
        if description:
            body["description"] = description
        if start:
            body["start"] = _moment(start, timezone)
        if end:
            body["end"] = _moment(end, timezone)
        if not body:
            return {"error": "nothing to update — pass summary, description, start, or end"}
        return gcal_result(
            email,
            calls.request(
                "PATCH",
                f"{_API}/calendars/{calendar_id}/events/{event_id}",
                headers=google_headers(profile["access_token"]),
                json=body,
            ),
        )

    def gcal_delete_event(event_id: str, calendar_id: str = "primary", account: str = "") -> dict[str, Any]:
        return done(send(account, "DELETE", f"{_API}/calendars/{calendar_id}/events/{event_id}"))

    string = {"type": "string"}
    return [
        attach(
            gcal_list_events,
            _schema(
                "gcal_list_events",
                "List Google Calendar events. time_min/time_max should be RFC3339 timestamps when provided.",
                {
                    "calendar_id": string,
                    "time_min": string,
                    "time_max": string,
                    "max_results": {"type": "integer"},
                },
                [],
            ),
            caps=["calendar", "read"],
        ),
        attach(
            gcal_free_busy,
            _schema(
                "gcal_free_busy",
                "Look up busy intervals (availability) for one or more calendars. "
                "time_min/time_max are RFC3339 timestamps; calendars is a comma-separated list of calendar ids.",
                {"time_min": string, "time_max": string, "calendars": string, "timezone": string},
                ["time_min", "time_max"],
            ),
            caps=["calendar", "read"],
        ),
        attach(
            gcal_create_event,
            _schema(
                "gcal_create_event",
                "Create a Google Calendar event. Requires user approval.",
                {
                    "summary": string,
                    "start": string,
                    "end": string,
                    "calendar_id": string,
                    "timezone": string,
                    "description": string,
                },
                ["summary", "start", "end"],
            ),
            approval=True,
            caps=["calendar", "write"],
        ),
        attach(
            gcal_update_event,
            _schema(
                "gcal_update_event",
                "Update fields of a Google Calendar event (only the provided fields change). Requires user approval.",
                {
                    "event_id": string,
                    "calendar_id": string,
                    "summary": string,
                    "start": string,
                    "end": string,
                    "timezone": string,
                    "description": string,
                },
                ["event_id"],
            ),
            approval=True,
            caps=["calendar", "write"],
        ),
        attach(
            gcal_delete_event,
            _schema(
                "gcal_delete_event",
                "Delete a Google Calendar event. Requires user approval.",
                {"event_id": string, "calendar_id": string},
                ["event_id"],
            ),
            approval=True,
            caps=["calendar", "write"],
        ),
    ]
