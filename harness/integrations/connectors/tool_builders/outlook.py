"""Outlook's calendar tools through Microsoft Graph; mail and people are in `outlook_mail`."""

from __future__ import annotations

import datetime
from typing import Any, Callable, Optional
from urllib.parse import quote

from .. import calls
from ..headers import graph_headers
from ..profiles import GENERIC_ACCOUNT_PROP, account_profile, account_result
from ..toolkit import attach, function_schema

_GRAPH = "https://graph.microsoft.com/v1.0"
_DEFAULT_PAGE = 10
_MAIL_PAGE_MAX = 20
_EVENTS_PAGE_MAX = 50
_DEFAULT_WINDOW_DAYS = 7
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# What a person says in `outlook_respond_event`, and the Graph action each one maps to.
_RESPONSES = {"accept": "accept", "decline": "decline", "tentative": "tentativelyAccept"}


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """The tool schema, with the `account` choice every Outlook tool shares."""
    return function_schema(name, description, {**properties, "account": GENERIC_ACCOUNT_PROP}, required)


def _text_body(text: str) -> dict[str, str]:
    return {"contentType": "Text", "content": text}


def _moment(when: str, timezone: str) -> dict[str, str]:
    return {"dateTime": when, "timeZone": timezone}


def _event_row(e: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {"id": e.get("id"), "subject": e.get("subject"),
                           "start": (e.get("start") or {}).get("dateTime"), "end": (e.get("end") or {}).get("dateTime")}
    if e.get("isAllDay"):
        row["all_day"] = True
    if (e.get("location") or {}).get("displayName"):
        row["location"] = e["location"]["displayName"]
    organizer = ((e.get("organizer") or {}).get("emailAddress") or {})
    if organizer.get("address"):
        row["organizer"] = organizer.get("name") or organizer["address"]
    if e.get("attendees"):
        row["attendees"] = len(e["attendees"])
    if (e.get("onlineMeeting") or {}).get("joinUrl"):
        row["join_url"] = e["onlineMeeting"]["joinUrl"]
    if e.get("isCancelled"):
        row["cancelled"] = True
    response = (e.get("responseStatus") or {}).get("response")
    if response and response not in ("none", "organizer"):
        row["my_response"] = response
    return row


def outlook_tools(secrets: Any, *, roots: Optional[list[Any]] = None) -> list[Callable[..., Any]]:
    def account_for(account: str):
        """`(account id, request headers, failure)` for the Outlook account asked for."""
        aid, profile, failure = account_profile(secrets, "outlook", account, "access_token")
        if failure:
            return None, None, failure
        return aid, graph_headers(profile["access_token"]), None

    def graph(account: str, method: str, path: str, **options: Any) -> dict[str, Any]:
        aid, headers, failure = account_for(account)
        if failure:
            return failure
        return account_result(aid, calls.request(method, _GRAPH + path, headers=headers, **options))

    def outlook_list_events(
        start: str = "", end: str = "", max_results: int = _DEFAULT_PAGE, account: str = ""
    ) -> dict[str, Any]:
        # `calendarView` expands recurrences and takes a window, which `/me/events` does neither of,
        # so a bare call used to return arbitrary and often past events. The default window is now
        # to seven days on.
        now = datetime.datetime.now(datetime.timezone.utc)
        window_end = now + datetime.timedelta(days=_DEFAULT_WINDOW_DAYS)
        params = {
            "startDateTime": start or now.strftime(_TIME_FORMAT),
            "endDateTime": end or window_end.strftime(_TIME_FORMAT),
            "$orderby": "start/dateTime",
            "$top": max(1, min(int(max_results or _DEFAULT_PAGE), _EVENTS_PAGE_MAX)),
        }
        params["$select"] = "id,subject,start,end,location,organizer,attendees,isOnlineMeeting,onlineMeeting,isCancelled,responseStatus,isAllDay,showAs"
        result = graph(account, "GET", "/me/calendarView", params=params)
        if not result.get("ok"):
            return result
        events = (result.get("data") or {}).get("value") or []
        return {**{k: v for k, v in result.items() if k != "data"}, "times": "UTC unless an event says otherwise",
                "events": [_event_row(e) for e in events]}

    def outlook_find_times(
        attendees: list[str], duration_minutes: int = 30, start: str = "", end: str = "", account: str = ""
    ) -> dict[str, Any]:
        # Graph's own scheduling assistant, which reads everyone's free/busy the account may see.
        # Work and school accounts only: a personal account answers that it is not supported.
        now = datetime.datetime.now(datetime.timezone.utc)
        body = {
            "attendees": [{"emailAddress": {"address": a.strip()}, "type": "required"} for a in attendees if a and a.strip()],
            "timeConstraint": {"timeslots": [{
                "start": {"dateTime": start or now.strftime(_TIME_FORMAT), "timeZone": "UTC"},
                "end": {"dateTime": end or (now + datetime.timedelta(days=_DEFAULT_WINDOW_DAYS)).strftime(_TIME_FORMAT), "timeZone": "UTC"},
            }]},
            "meetingDuration": f"PT{max(5, int(duration_minutes or 30))}M",
            "maxCandidates": 10,
            "returnSuggestionReasons": True,
        }
        result = graph(account, "POST", "/me/findMeetingTimes", json=body)
        if not result.get("ok"):
            return result
        found = result.get("data") or {}
        return {**{k: v for k, v in result.items() if k != "data"},
                "times": [{"start": (t.get("meetingTimeSlot") or {}).get("start", {}).get("dateTime"),
                           "end": (t.get("meetingTimeSlot") or {}).get("end", {}).get("dateTime"),
                           "confidence": t.get("confidence"), "why": t.get("suggestionReason")}
                          for t in found.get("meetingTimeSuggestions") or []],
                **({"none_because": found["emptySuggestionsReason"]} if found.get("emptySuggestionsReason") else {})}

    def outlook_create_event(
        subject: str,
        start: str,
        end: str,
        timezone: str = "UTC",
        body: str = "",
        attendees: str = "",
        location: str = "",
        teams_meeting: bool = False,
        account: str = "",
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "subject": subject,
            "body": _text_body(body),
            "start": _moment(start, timezone),
            "end": _moment(end, timezone),
        }
        if attendees:
            event["attendees"] = [
                {"emailAddress": {"address": address.strip()}, "type": "required"}
                for address in attendees.split(",")
                if address.strip()
            ]
        if location:
            event["location"] = {"displayName": location}
        if teams_meeting:
            event["isOnlineMeeting"] = True
            event["onlineMeetingProvider"] = "teamsForBusiness"
        return graph(account, "POST", "/me/events", json=event)

    def outlook_update_event(
        event_id: str,
        subject: str = "",
        start: str = "",
        end: str = "",
        timezone: str = "UTC",
        body: str = "",
        location: str = "",
        account: str = "",
    ) -> dict[str, Any]:
        # PATCH semantics: only the fields that were given change.
        changes: dict[str, Any] = {}
        if subject:
            changes["subject"] = subject
        if body:
            changes["body"] = _text_body(body)
        if start:
            changes["start"] = _moment(start, timezone)
        if end:
            changes["end"] = _moment(end, timezone)
        if location:
            changes["location"] = {"displayName": location}
        return graph(account, "PATCH", f"/me/events/{quote(event_id)}", json=changes)

    def outlook_delete_event(event_id: str, account: str = "") -> dict[str, Any]:
        return graph(account, "DELETE", f"/me/events/{quote(event_id)}")

    def outlook_respond_event(event_id: str, response: str, comment: str = "", account: str = "") -> dict[str, Any]:
        aid, headers, failure = account_for(account)
        if failure:
            return failure
        action = _RESPONSES.get((response or "").strip().lower())
        if not action:
            return {"error": "response must be one of: accept, decline, tentative"}
        return account_result(
            aid,
            calls.request(
                "POST",
                f"{_GRAPH}/me/events/{quote(event_id)}/{action}",
                headers=headers,
                json={"comment": comment, "sendResponse": True},
            ),
        )

    string = {"type": "string"}
    return [
        attach(
            outlook_list_events,
            _schema(
                "outlook_list_events",
                "List upcoming Outlook calendar events (recurrences expanded, ordered "
                "by start). start/end are ISO timestamps; default window is the next "
                "7 days.",
                {"start": string, "end": string, "max_results": {"type": "integer"}},
                [],
            ),
            caps=["outlook", "read"],
        ),
        attach(
            outlook_create_event,
            _schema(
                "outlook_create_event",
                "Create an Outlook calendar event; invites go to attendees "
                "(comma-separated emails). teams_meeting adds a Teams link. "
                "Requires user approval.",
                {
                    "subject": string,
                    "start": string,
                    "end": string,
                    "timezone": string,
                    "body": string,
                    "attendees": string,
                    "location": string,
                    "teams_meeting": {"type": "boolean"},
                },
                ["subject", "start", "end"],
            ),
            approval=True,
            caps=["outlook", "write"],
        ),
        attach(
            outlook_update_event,
            _schema(
                "outlook_update_event",
                "Change fields of an existing Outlook calendar event (only the "
                "provided fields change). Requires user approval.",
                {
                    "event_id": string,
                    "subject": string,
                    "start": string,
                    "end": string,
                    "timezone": string,
                    "body": string,
                    "location": string,
                },
                ["event_id"],
            ),
            approval=True,
            caps=["outlook", "write"],
        ),
        attach(
            outlook_delete_event,
            _schema(
                "outlook_delete_event",
                "Delete (cancel) an Outlook calendar event. Requires user approval.",
                {"event_id": string},
                ["event_id"],
            ),
            approval=True,
            caps=["outlook", "write"],
        ),
        attach(
            outlook_find_times,
            _schema(
                "outlook_find_times",
                "Find times when everyone is free for a meeting of duration_minutes between start and end "
                "(ISO, UTC; default the next 7 days). Work and school accounts only.",
                {"attendees": {"type": "array", "items": {"type": "string"}}, "duration_minutes": {"type": "integer"},
                 "start": string, "end": string},
                ["attendees"],
            ),
            caps=["outlook", "read"],
        ),
        attach(
            outlook_respond_event,
            _schema(
                "outlook_respond_event",
                "Respond to an Outlook meeting invite: accept, decline, or "
                "tentative. The organizer is notified. Requires user approval.",
                {"event_id": string, "response": string, "comment": string},
                ["event_id", "response"],
            ),
            approval=True,
            caps=["outlook", "write"],
        ),
    ]
