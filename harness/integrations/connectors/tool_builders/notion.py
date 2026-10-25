"""Integration tools: notion (tách từ integration_tools.py, hành vi giữ nguyên)."""
from __future__ import annotations

import json
from .. import calls
from ..toolkit import attach, function_schema, clamp
from ..profiles import account_profile, account_result, GENERIC_ACCOUNT_PROP

from typing import Any, Callable, Optional


def notion_tools(secrets: Any, *, roots: Optional[list[Any]] = None) -> list[Callable[..., Any]]:
    tools: list[Callable[..., Any]] = []
    def _notion_headers(profile: dict[str, Any]) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {profile['access_token']}",
            "Notion-Version": "2022-06-28",
        }

    def _notion_blocks_text(blocks: list[dict]) -> str:
        """Flatten block children to readable lines (rich_text plain_text)."""
        lines = []
        for b in blocks:
            content = b.get(b.get("type", ""), {})
            texts = content.get("rich_text") or content.get("title") or []
            line = "".join(
                t.get("plain_text", "") for t in texts if isinstance(t, dict)
            )
            if line:
                lines.append(line)
        return "\n".join(lines)

    def notion_search(
        query: str, max_results: int = 10, account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = account_profile(secrets, "notion", account, "access_token")
        if err:
            return err
        result = calls.request(
            "POST",
            "https://api.notion.com/v1/search",
            headers=_notion_headers(profile),
            json={"query": query, "page_size": clamp(max_results, ceiling=100)},
        )
        return account_result(aid, result)

    notion_search.__name__ = "notion_search"
    tools.append(
        attach(
            notion_search,
            function_schema(
                "notion_search",
                "Search Notion pages and databases the integration can see.",
                {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": GENERIC_ACCOUNT_PROP,
                },
                ["query"],
            ),
            caps=["notion", "read"],
        )
    )

    def notion_read_page(page_id: str, account: str = "") -> dict[str, Any]:
        aid, profile, err = account_profile(secrets, "notion", account, "access_token")
        if err:
            return err
        page = calls.request(
            "GET",
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=_notion_headers(profile),
        )
        if "error" in page:
            return account_result(aid, page)
        blocks = calls.request(
            "GET",
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=_notion_headers(profile),
            params={"page_size": 100},
        )
        text = (
            _notion_blocks_text((blocks.get("data") or {}).get("results") or [])
            if "error" not in blocks
            else ""
        )
        return account_result(
            aid,
            {
                "ok": True,
                "properties": (page.get("data") or {}).get("properties"),
                "url": (page.get("data") or {}).get("url"),
                "text": text,
            },
        )

    notion_read_page.__name__ = "notion_read_page"
    tools.append(
        attach(
            notion_read_page,
            function_schema(
                "notion_read_page",
                "Read a Notion page: properties plus its content flattened to text.",
                {"page_id": {"type": "string"}, "account": GENERIC_ACCOUNT_PROP},
                ["page_id"],
            ),
            caps=["notion", "read"],
        )
    )

    def notion_query_database(
        database_id: str,
        filter_json: str = "",
        max_results: int = 10,
        account: str = "",
    ) -> dict[str, Any]:
        aid, profile, err = account_profile(secrets, "notion", account, "access_token")
        if err:
            return err
        body: dict[str, Any] = {"page_size": clamp(max_results, ceiling=100)}
        if filter_json:
            try:
                body["filter"] = json.loads(filter_json)
            except ValueError:
                return {"error": "filter_json must be a Notion filter object (JSON)"}
        result = calls.request(
            "POST",
            f"https://api.notion.com/v1/databases/{database_id}/query",
            headers=_notion_headers(profile),
            json=body,
        )
        return account_result(aid, result)

    notion_query_database.__name__ = "notion_query_database"
    tools.append(
        attach(
            notion_query_database,
            function_schema(
                "notion_query_database",
                "Query a Notion database, optionally with a Notion filter object.",
                {
                    "database_id": {"type": "string"},
                    "filter_json": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": GENERIC_ACCOUNT_PROP,
                },
                ["database_id"],
            ),
            caps=["notion", "read"],
        )
    )

    def notion_create_page(
        parent_page_id: str, title: str, content: str = "", account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = account_profile(secrets, "notion", account, "access_token")
        if err:
            return err
        children = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"text": {"content": line}}]},
            }
            for line in content.splitlines()
            if line.strip()
        ]
        result = calls.request(
            "POST",
            "https://api.notion.com/v1/pages",
            headers=_notion_headers(profile),
            json={
                "parent": {"page_id": parent_page_id},
                "properties": {"title": {"title": [{"text": {"content": title}}]}},
                "children": children,
            },
        )
        return account_result(aid, result)

    notion_create_page.__name__ = "notion_create_page"
    tools.append(
        attach(
            notion_create_page,
            function_schema(
                "notion_create_page",
                "Create a Notion page under a parent page (plain-text paragraphs).",
                {
                    "parent_page_id": {"type": "string"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "account": GENERIC_ACCOUNT_PROP,
                },
                ["parent_page_id", "title"],
            ),
            approval=True,
            caps=["notion", "write"],
        )
    )
    return tools
