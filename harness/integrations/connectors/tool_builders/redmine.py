"""Integration tools: redmine (tách từ integration_tools.py, hành vi giữ nguyên)."""
from __future__ import annotations

from .. import calls
from ..toolkit import attach, function_schema
from ..profiles import default_profile
from ..headers import redmine_base, redmine_headers

from typing import Any, Callable, Optional


def redmine_tools(secrets: Any, *, roots: Optional[list[Any]] = None) -> list[Callable[..., Any]]:
    tools: list[Callable[..., Any]] = []
    def redmine_search_issues(
        query: str = "",
        project_id: str = "",
        status: str = "open",
        assigned_to_me: bool = False,
        max_results: int = 25,
    ) -> dict[str, Any]:
        profile, err = default_profile(secrets, "redmine", "base_url", "api_key")
        if err:
            return err
        params: dict[str, Any] = {
            "limit": max(1, min(int(max_results or 25), 100)),
            "sort": "updated_on:desc",
        }
        # Redmine's own filter syntax: `~` is "contains". Kept on the stable
        # /issues.json endpoint rather than /search.json, which is still alpha.
        if query:
            params["subject"] = f"~{query}"
        if project_id:
            params["project_id"] = project_id
        params["status_id"] = {"open": "open", "closed": "closed"}.get(
            str(status or "open").lower(), "*"
        )
        # `me` is a literal Redmine accepts in filters — no user lookup needed.
        if assigned_to_me:
            params["assigned_to_id"] = "me"
        return calls.request(
            "GET",
            f"{redmine_base(profile)}/issues.json",
            headers=redmine_headers(profile),
            params=params,
        )

    redmine_search_issues.__name__ = "redmine_search_issues"
    tools.append(
        attach(
            redmine_search_issues,
            function_schema(
                "redmine_search_issues",
                "Search Redmine issues by subject, project, or assignee.",
                {
                    "query": {"type": "string", "description": "Matches the subject."},
                    "project_id": {
                        "type": "string",
                        "description": "Project id or identifier.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["open", "closed", "any"],
                        "description": "Defaults to open.",
                    },
                    "assigned_to_me": {"type": "boolean"},
                    "max_results": {"type": "integer"},
                },
                [],
            ),
            caps=["redmine", "read"],
        )
    )

    def redmine_get_issue(issue_id: int) -> dict[str, Any]:
        profile, err = default_profile(secrets, "redmine", "base_url", "api_key")
        if err:
            return err
        return calls.request(
            "GET",
            f"{redmine_base(profile)}/issues/{int(issue_id)}.json",
            headers=redmine_headers(profile),
            # journals = the comment thread; without it the issue reads as a stub.
            params={"include": "journals,attachments,relations"},
        )

    redmine_get_issue.__name__ = "redmine_get_issue"
    tools.append(
        attach(
            redmine_get_issue,
            function_schema(
                "redmine_get_issue",
                "Read a Redmine issue with its comments and attachments.",
                {"issue_id": {"type": "integer"}},
                ["issue_id"],
            ),
            caps=["redmine", "read"],
        )
    )

    def redmine_create_issue(
        project_id: str,
        subject: str,
        description: str = "",
        tracker_id: int = 0,
        priority_id: int = 0,
        assigned_to_id: int = 0,
    ) -> dict[str, Any]:
        profile, err = default_profile(secrets, "redmine", "base_url", "api_key")
        if err:
            return err
        issue: dict[str, Any] = {"project_id": project_id, "subject": subject}
        if description:
            issue["description"] = description
        for key, value in (
            ("tracker_id", tracker_id),
            ("priority_id", priority_id),
            ("assigned_to_id", assigned_to_id),
        ):
            if value:
                issue[key] = int(value)
        return calls.request(
            "POST",
            f"{redmine_base(profile)}/issues.json",
            headers=redmine_headers(profile),
            json={"issue": issue},
        )

    redmine_create_issue.__name__ = "redmine_create_issue"
    tools.append(
        attach(
            redmine_create_issue,
            function_schema(
                "redmine_create_issue",
                "Create a Redmine issue. Requires user approval.",
                {
                    "project_id": {
                        "type": "string",
                        "description": "Project id or identifier.",
                    },
                    "subject": {"type": "string"},
                    "description": {"type": "string"},
                    "tracker_id": {
                        "type": "integer",
                        "description": "Numeric id from redmine_list_enumerations.",
                    },
                    "priority_id": {
                        "type": "integer",
                        "description": "Numeric id from redmine_list_enumerations.",
                    },
                    "assigned_to_id": {
                        "type": "integer",
                        "description": "Numeric user id; redmine_current_user gives yours.",
                    },
                },
                ["project_id", "subject"],
            ),
            approval=True,
            caps=["redmine", "write"],
        )
    )

    def redmine_update_issue(
        issue_id: int,
        subject: str = "",
        description: str = "",
        status_id: int = 0,
        priority_id: int = 0,
        assigned_to_id: int = 0,
        done_ratio: int = -1,
    ) -> dict[str, Any]:
        profile, err = default_profile(secrets, "redmine", "base_url", "api_key")
        if err:
            return err
        issue: dict[str, Any] = {}
        if subject:
            issue["subject"] = subject
        if description:
            issue["description"] = description
        for key, value in (
            ("status_id", status_id),
            ("priority_id", priority_id),
            ("assigned_to_id", assigned_to_id),
        ):
            if value:
                issue[key] = int(value)
        # 0 is a legitimate done_ratio ("not started"), so the unset marker is -1.
        if int(done_ratio) >= 0:
            issue["done_ratio"] = max(0, min(int(done_ratio), 100))
        if not issue:
            return {"error": "nothing to update"}
        return calls.request(
            "PUT",
            f"{redmine_base(profile)}/issues/{int(issue_id)}.json",
            headers=redmine_headers(profile),
            json={"issue": issue},
        )

    redmine_update_issue.__name__ = "redmine_update_issue"
    tools.append(
        attach(
            redmine_update_issue,
            function_schema(
                "redmine_update_issue",
                "Change a Redmine issue's fields or status. Requires user approval.",
                {
                    "issue_id": {"type": "integer"},
                    "subject": {"type": "string"},
                    "description": {"type": "string"},
                    "status_id": {
                        "type": "integer",
                        "description": "Numeric id from redmine_list_enumerations.",
                    },
                    "priority_id": {
                        "type": "integer",
                        "description": "Numeric id from redmine_list_enumerations.",
                    },
                    "assigned_to_id": {"type": "integer"},
                    "done_ratio": {
                        "type": "integer",
                        "description": "Percent complete, 0-100.",
                    },
                },
                ["issue_id"],
            ),
            approval=True,
            caps=["redmine", "write"],
        )
    )

    def redmine_comment_issue(issue_id: int, notes: str) -> dict[str, Any]:
        profile, err = default_profile(secrets, "redmine", "base_url", "api_key")
        if err:
            return err
        return calls.request(
            "PUT",
            f"{redmine_base(profile)}/issues/{int(issue_id)}.json",
            headers=redmine_headers(profile),
            json={"issue": {"notes": notes}},
        )

    redmine_comment_issue.__name__ = "redmine_comment_issue"
    tools.append(
        attach(
            redmine_comment_issue,
            function_schema(
                "redmine_comment_issue",
                "Add a comment to a Redmine issue. Requires user approval.",
                {"issue_id": {"type": "integer"}, "notes": {"type": "string"}},
                ["issue_id", "notes"],
            ),
            approval=True,
            caps=["redmine", "write"],
        )
    )

    def redmine_list_projects(max_results: int = 50) -> dict[str, Any]:
        profile, err = default_profile(secrets, "redmine", "base_url", "api_key")
        if err:
            return err
        return calls.request(
            "GET",
            f"{redmine_base(profile)}/projects.json",
            headers=redmine_headers(profile),
            params={"limit": max(1, min(int(max_results or 50), 100))},
        )

    redmine_list_projects.__name__ = "redmine_list_projects"
    tools.append(
        attach(
            redmine_list_projects,
            function_schema(
                "redmine_list_projects",
                "List the Redmine projects your account can see.",
                {"max_results": {"type": "integer"}},
                [],
            ),
            caps=["redmine", "read"],
        )
    )

    def redmine_get_project(project_id: str) -> dict[str, Any]:
        profile, err = default_profile(secrets, "redmine", "base_url", "api_key")
        if err:
            return err
        return calls.request(
            "GET",
            f"{redmine_base(profile)}/projects/{project_id}.json",
            headers=redmine_headers(profile),
            params={"include": "trackers,issue_categories"},
        )

    redmine_get_project.__name__ = "redmine_get_project"
    tools.append(
        attach(
            redmine_get_project,
            function_schema(
                "redmine_get_project",
                "Read a Redmine project, with its trackers and categories.",
                {
                    "project_id": {
                        "type": "string",
                        "description": "Project id or identifier.",
                    }
                },
                ["project_id"],
            ),
            caps=["redmine", "read"],
        )
    )

    def redmine_list_time_entries(
        project_id: str = "",
        issue_id: int = 0,
        from_date: str = "",
        to_date: str = "",
        max_results: int = 25,
    ) -> dict[str, Any]:
        profile, err = default_profile(secrets, "redmine", "base_url", "api_key")
        if err:
            return err
        params: dict[str, Any] = {"limit": max(1, min(int(max_results or 25), 100))}
        if project_id:
            params["project_id"] = project_id
        if issue_id:
            params["issue_id"] = int(issue_id)
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        return calls.request(
            "GET",
            f"{redmine_base(profile)}/time_entries.json",
            headers=redmine_headers(profile),
            params=params,
        )

    redmine_list_time_entries.__name__ = "redmine_list_time_entries"
    tools.append(
        attach(
            redmine_list_time_entries,
            function_schema(
                "redmine_list_time_entries",
                "List logged time, optionally by project, issue, or date range.",
                {
                    "project_id": {"type": "string"},
                    "issue_id": {"type": "integer"},
                    "from_date": {"type": "string", "description": "YYYY-MM-DD."},
                    "to_date": {"type": "string", "description": "YYYY-MM-DD."},
                    "max_results": {"type": "integer"},
                },
                [],
            ),
            caps=["redmine", "read"],
        )
    )

    def redmine_log_time(
        hours: float,
        issue_id: int = 0,
        project_id: str = "",
        comments: str = "",
        spent_on: str = "",
        activity_id: int = 0,
    ) -> dict[str, Any]:
        profile, err = default_profile(secrets, "redmine", "base_url", "api_key")
        if err:
            return err
        # Redmine takes exactly one of the two; saying so here beats a 422 the model
        # then has to decode.
        if bool(issue_id) == bool(project_id):
            return {"error": "give either issue_id or project_id, not both"}
        entry: dict[str, Any] = {"hours": float(hours)}
        if issue_id:
            entry["issue_id"] = int(issue_id)
        else:
            entry["project_id"] = project_id
        if comments:
            entry["comments"] = comments
        if spent_on:
            entry["spent_on"] = spent_on
        if activity_id:
            entry["activity_id"] = int(activity_id)
        return calls.request(
            "POST",
            f"{redmine_base(profile)}/time_entries.json",
            headers=redmine_headers(profile),
            json={"time_entry": entry},
        )

    redmine_log_time.__name__ = "redmine_log_time"
    tools.append(
        attach(
            redmine_log_time,
            function_schema(
                "redmine_log_time",
                "Log time against a Redmine issue or project. Requires user approval.",
                {
                    "hours": {"type": "number"},
                    "issue_id": {"type": "integer"},
                    "project_id": {"type": "string"},
                    "comments": {"type": "string"},
                    "spent_on": {
                        "type": "string",
                        "description": "YYYY-MM-DD; defaults to today.",
                    },
                    "activity_id": {
                        "type": "integer",
                        "description": "Numeric id from redmine_list_enumerations.",
                    },
                },
                ["hours"],
            ),
            approval=True,
            caps=["redmine", "write"],
        )
    )

    def redmine_list_enumerations() -> dict[str, Any]:
        profile, err = default_profile(secrets, "redmine", "base_url", "api_key")
        if err:
            return err
        base = redmine_base(profile)
        headers = redmine_headers(profile)
        out: dict[str, Any] = {}
        # Four calls behind one tool: every write tool wants numeric ids and a model
        # cannot guess them. These endpoints are `alpha` in Redmine's own docs and an
        # older site may not serve all of them, so a failure is reported per key
        # instead of sinking the whole call.
        for key, path in (
            ("trackers", "/trackers.json"),
            ("issue_statuses", "/issue_statuses.json"),
            ("issue_priorities", "/enumerations/issue_priorities.json"),
            ("time_entry_activities", "/enumerations/time_entry_activities.json"),
        ):
            res = calls.request("GET", f"{base}{path}", headers=headers)
            out[key] = res.get("data") if res.get("ok") else {"error": res.get("error")}
        return {"ok": True, "data": out}

    redmine_list_enumerations.__name__ = "redmine_list_enumerations"
    tools.append(
        attach(
            redmine_list_enumerations,
            function_schema(
                "redmine_list_enumerations",
                "List the numeric ids Redmine expects for tracker, status, priority "
                "and time-entry activity.",
                {},
                [],
            ),
            caps=["redmine", "read"],
        )
    )

    def redmine_current_user() -> dict[str, Any]:
        profile, err = default_profile(secrets, "redmine", "base_url", "api_key")
        if err:
            return err
        return calls.request(
            "GET",
            f"{redmine_base(profile)}/users/current.json",
            headers=redmine_headers(profile),
        )

    redmine_current_user.__name__ = "redmine_current_user"
    tools.append(
        attach(
            redmine_current_user,
            function_schema(
                "redmine_current_user",
                "Read the Redmine account this connection uses, including its user id.",
                {},
                [],
            ),
            caps=["redmine", "read"],
        )
    )
    return tools
