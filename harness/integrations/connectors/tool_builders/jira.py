"""Integration tools: jira (tách từ integration_tools.py, hành vi giữ nguyên)."""
from __future__ import annotations

from .. import calls
from ..toolkit import attach, function_schema
from ..profiles import default_profile
from ..headers import basic_auth, atlassian_base

from typing import Any, Callable, Optional


def jira_tools(secrets: Any, *, roots: Optional[list[Any]] = None) -> list[Callable[..., Any]]:
    tools: list[Callable[..., Any]] = []
    def jira_search_issues(jql: str, max_results: int = 10) -> dict[str, Any]:
        profile, err = default_profile(secrets, "jira", "base_url", "email", "api_token")
        if err:
            return err
        return calls.request(
            "GET",
            f"{atlassian_base(profile)}/rest/api/3/search",
            auth=basic_auth(profile["email"], profile["api_token"]),
            params={"jql": jql, "maxResults": max(1, min(int(max_results or 10), 20))},
        )

    jira_search_issues.__name__ = "jira_search_issues"
    tools.append(
        attach(
            jira_search_issues,
            function_schema(
                "jira_search_issues",
                "Search Jira issues using JQL.",
                {"jql": {"type": "string"}, "max_results": {"type": "integer"}},
                ["jql"],
            ),
            caps=["jira", "read"],
        )
    )

    def jira_get_issue(issue_key: str) -> dict[str, Any]:
        profile, err = default_profile(secrets, "jira", "base_url", "email", "api_token")
        if err:
            return err
        return calls.request(
            "GET",
            f"{atlassian_base(profile)}/rest/api/3/issue/{issue_key}",
            auth=basic_auth(profile["email"], profile["api_token"]),
        )

    jira_get_issue.__name__ = "jira_get_issue"
    tools.append(
        attach(
            jira_get_issue,
            function_schema(
                "jira_get_issue",
                "Read a Jira issue.",
                {"issue_key": {"type": "string"}},
                ["issue_key"],
            ),
            caps=["jira", "read"],
        )
    )

    def jira_create_issue(
        project_key: str, issue_type: str, summary: str, description: str = ""
    ) -> dict[str, Any]:
        profile, err = default_profile(secrets, "jira", "base_url", "email", "api_token")
        if err:
            return err
        payload = {
            "fields": {
                "project": {"key": project_key},
                "issuetype": {"name": issue_type},
                "summary": summary,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": description or summary}
                            ],
                        }
                    ],
                },
            }
        }
        return calls.request(
            "POST",
            f"{atlassian_base(profile)}/rest/api/3/issue",
            auth=basic_auth(profile["email"], profile["api_token"]),
            json=payload,
        )

    jira_create_issue.__name__ = "jira_create_issue"
    tools.append(
        attach(
            jira_create_issue,
            function_schema(
                "jira_create_issue",
                "Create a Jira issue. Requires user approval.",
                {
                    "project_key": {"type": "string"},
                    "issue_type": {"type": "string"},
                    "summary": {"type": "string"},
                    "description": {"type": "string"},
                },
                ["project_key", "issue_type", "summary"],
            ),
            approval=True,
            caps=["jira", "write"],
        )
    )
    return tools
