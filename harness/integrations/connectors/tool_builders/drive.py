"""Integration tools: drive (tách từ integration_tools.py, hành vi giữ nguyên)."""
from __future__ import annotations

import json
from urllib.parse import quote
from .. import calls
from ..toolkit import attach, function_schema, clamp
from ..profiles import default_profile, account_profile, account_result, GENERIC_ACCOUNT_PROP
from ..headers import google_headers, bearer_headers

from typing import Any, Callable, Optional


_CANVA = "https://api.canva.com/rest/v1"


def drive_tools(secrets: Any, *, roots: Optional[list[Any]] = None) -> list[Callable[..., Any]]:
    tools: list[Callable[..., Any]] = []
    _DRIVE = "https://www.googleapis.com/drive/v3"
    _DRIVE_FIELDS = "files(id,name,mimeType,modifiedTime,size,webViewLink)"
    # Google-native types export to text; everything else downloads as-is.
    _DRIVE_EXPORTS = {
        "application/vnd.google-apps.document": "text/plain",
        "application/vnd.google-apps.spreadsheet": "text/csv",
        "application/vnd.google-apps.presentation": "text/plain",
    }

    def _drive_quote(term: str) -> str:
        return term.replace("\\", "\\\\").replace("'", "\\'")

    def drive_search_files(
        query: str, max_results: int = 10, account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = account_profile(
            secrets, "google_drive", account, "access_token"
        )
        if err:
            return err
        q = _drive_quote(query)
        return account_result(
            aid,
            calls.request(
                "GET",
                f"{_DRIVE}/files",
                headers=google_headers(profile["access_token"]),
                params={
                    "q": f"(name contains '{q}' or fullText contains '{q}') and trashed=false",
                    "pageSize": clamp(max_results),
                    "fields": _DRIVE_FIELDS,
                },
            ),
        )

    drive_search_files.__name__ = "drive_search_files"
    tools.append(
        attach(
            drive_search_files,
            function_schema(
                "drive_search_files",
                "Search Google Drive files by name or content.",
                {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": GENERIC_ACCOUNT_PROP,
                },
                ["query"],
            ),
            caps=["google_drive", "read"],
        )
    )

    def drive_list_folder(
        folder_id: str = "root", max_results: int = 20, account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = account_profile(
            secrets, "google_drive", account, "access_token"
        )
        if err:
            return err
        return account_result(
            aid,
            calls.request(
                "GET",
                f"{_DRIVE}/files",
                headers=google_headers(profile["access_token"]),
                params={
                    "q": f"'{_drive_quote(folder_id)}' in parents and trashed=false",
                    "pageSize": clamp(max_results, default=20, ceiling=50),
                    "fields": _DRIVE_FIELDS,
                },
            ),
        )

    drive_list_folder.__name__ = "drive_list_folder"
    tools.append(
        attach(
            drive_list_folder,
            function_schema(
                "drive_list_folder",
                "List a Google Drive folder's contents ('root' for My Drive).",
                {
                    "folder_id": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": GENERIC_ACCOUNT_PROP,
                },
                [],
            ),
            caps=["google_drive", "read"],
        )
    )

    def drive_read_file(
        file_id: str, max_chars: int = 20000, account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = account_profile(
            secrets, "google_drive", account, "access_token"
        )
        if err:
            return err
        headers = google_headers(profile["access_token"])
        meta = calls.request(
            "GET",
            f"{_DRIVE}/files/{quote(file_id)}",
            headers=headers,
            params={"fields": "id,name,mimeType,size"},
        )
        if not meta.get("ok"):
            return account_result(aid, meta)
        info = meta.get("data") or {}
        mime = str(info.get("mimeType", ""))
        export_mime = _DRIVE_EXPORTS.get(mime)
        if export_mime:
            body = calls.request(
                "GET",
                f"{_DRIVE}/files/{quote(file_id)}/export",
                headers=headers,
                params={"mimeType": export_mime},
            )
        elif mime.startswith("application/vnd.google-apps"):
            return account_result(
                aid, {"error": f"cannot read {mime} as text", "file": info}
            )
        else:
            body = calls.request(
                "GET",
                f"{_DRIVE}/files/{quote(file_id)}",
                headers=headers,
                params={"alt": "media"},
            )
        if not body.get("ok"):
            return account_result(aid, body)
        text = body.get("data")
        if not isinstance(text, str):
            text = json.dumps(text)
        return account_result(
            aid,
            {
                "ok": True,
                "file": info,
                "content": text[: max(1, int(max_chars))],
                "truncated": len(text) > max_chars,
            },
        )

    drive_read_file.__name__ = "drive_read_file"
    tools.append(
        attach(
            drive_read_file,
            function_schema(
                "drive_read_file",
                "Read a Drive file as text (Docs/Sheets/Slides export; other text files download).",
                {
                    "file_id": {"type": "string"},
                    "max_chars": {"type": "integer"},
                    "account": GENERIC_ACCOUNT_PROP,
                },
                ["file_id"],
            ),
            caps=["google_drive", "read"],
        )
    )

    def canva_list_designs(query: str = "", max_results: int = 10) -> dict[str, Any]:
        profile, err = default_profile(secrets, "canva", "access_token")
        if err:
            return err
        params: dict[str, Any] = {"limit": clamp(max_results)}
        if query:
            params["query"] = query
        return calls.request(
            "GET",
            f"{_CANVA}/designs",
            headers=bearer_headers(profile["access_token"]),
            params=params,
        )

    canva_list_designs.__name__ = "canva_list_designs"
    tools.append(
        attach(
            canva_list_designs,
            function_schema(
                "canva_list_designs",
                "List (or text-search) Canva designs.",
                {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                [],
            ),
            caps=["canva", "read"],
        )
    )

    def canva_get_design(design_id: str) -> dict[str, Any]:
        profile, err = default_profile(secrets, "canva", "access_token")
        if err:
            return err
        return calls.request(
            "GET",
            f"{_CANVA}/designs/{quote(design_id)}",
            headers=bearer_headers(profile["access_token"]),
        )

    canva_get_design.__name__ = "canva_get_design"
    tools.append(
        attach(
            canva_get_design,
            function_schema(
                "canva_get_design",
                "Read a Canva design's metadata (title, pages, urls).",
                {"design_id": {"type": "string"}},
                ["design_id"],
            ),
            caps=["canva", "read"],
        )
    )

    def canva_export_design(design_id: str, format: str = "pdf") -> dict[str, Any]:
        profile, err = default_profile(secrets, "canva", "access_token")
        if err:
            return err
        return calls.request(
            "POST",
            f"{_CANVA}/exports",
            headers=bearer_headers(profile["access_token"]),
            json={"design_id": design_id, "format": {"type": format}},
        )

    canva_export_design.__name__ = "canva_export_design"
    tools.append(
        attach(
            canva_export_design,
            function_schema(
                "canva_export_design",
                "Start rendering a Canva design to pdf/png/jpg; returns an export job to poll.",
                {"design_id": {"type": "string"}, "format": {"type": "string"}},
                ["design_id"],
            ),
            caps=["canva", "read"],
        )
    )

    def canva_get_export(export_id: str) -> dict[str, Any]:
        profile, err = default_profile(secrets, "canva", "access_token")
        if err:
            return err
        return calls.request(
            "GET",
            f"{_CANVA}/exports/{quote(export_id)}",
            headers=bearer_headers(profile["access_token"]),
        )

    canva_get_export.__name__ = "canva_get_export"
    tools.append(
        attach(
            canva_get_export,
            function_schema(
                "canva_get_export",
                "Check a Canva export job; returns download URLs when finished.",
                {"export_id": {"type": "string"}},
                ["export_id"],
            ),
            caps=["canva", "read"],
        )
    )


    # ---- Google Workspace, hand-written REST -----------------------------------------
    # Google runs remote MCP servers for all of these, and we reached them: they answer
    # `initialize` and `tools/list` to an anonymous caller. Every `tools/call` is refused
    # unless the Cloud project is enrolled in the Workspace Developer Preview Program, so
    # the tools below call the product APIs, which carry no such gate. The surface
    # deliberately mirrors the MCP one, tool for tool, so a later switch is a swap and not
    # a redesign.
    return tools
