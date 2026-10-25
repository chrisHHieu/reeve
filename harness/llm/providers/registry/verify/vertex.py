"""The Test button for Vertex AI."""

from __future__ import annotations

from typing import Any

from ...vertex.credentials import SCOPES, regional_host

#: The probe is `countTokens` on a stable Gemini model. It is free (nothing is generated),
#: works with plain ADC (the model list and GET endpoints return 403 or 404 under user
#: credentials), and exercises project, location and API enablement in one call.
PROBE_MODEL = "gemini-2.5-flash"
PROBE_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


def _count_tokens(url: str, headers: dict[str, str], timeout: float):
    import httpx

    return httpx.post(url, headers=headers, json=PROBE_BODY, timeout=timeout)


def _verify_api_key(key: str, timeout: float) -> dict[str, Any]:
    try:
        # Express mode is global: no region host, and no project in the path.
        response = _count_tokens(
            f"https://aiplatform.googleapis.com/v1/publishers/google/models/{PROBE_MODEL}:countTokens",
            {"x-goog-api-key": key},
            timeout,
        )
    except Exception as exc:
        return {"ok": False, "error": f"Couldn't reach Vertex AI ({exc.__class__.__name__})."}
    if response.status_code < 300:
        return {"ok": True}
    if response.status_code in (401, 403):
        return {"ok": False, "error": "Google rejected the API key."}
    return {"ok": False, "error": f"Vertex AI returned HTTP {response.status_code}."}


def _credentials_error(exc: Exception) -> str:
    kind = exc.__class__.__name__
    if kind == "DefaultCredentialsError":
        return (
            "No Google Cloud credentials found — paste a service-account "
            "JSON, or run `gcloud auth application-default login` first."
        )
    if kind in ("RefreshError", "MalformedError", "JSONDecodeError", "ValueError"):
        return "Google rejected the credentials."
    return f"Couldn't load Google credentials ({kind})."


def verify_vertex(fields: dict[str, Any], timeout: float) -> dict[str, Any]:
    """One `countTokens` call through the selected method: an ADC or service-account bearer
    token, or the express API key header."""
    from ...vertex.credentials import load_credentials

    project = (fields.get("project") or "").strip()
    location = (fields.get("location") or "").strip()
    service_account = (fields.get("service_account_json") or "").strip()
    method = (fields.get("auth_method") or "").strip() or ("service_account" if service_account else "adc")

    if method == "api_key":
        key = (fields.get("vertex_api_key") or "").strip()
        return _verify_api_key(key, timeout) if key else {"ok": False, "error": "Enter a Vertex API key to test."}
    if method == "service_account" and not service_account:
        return {"ok": False, "error": "Paste a service-account JSON to test."}

    try:
        credentials = load_credentials(fields.get("service_account_json")) if method == "service_account" else None
        if credentials is None:
            import google.auth

            credentials, _ = google.auth.default(scopes=SCOPES)
        from google.auth.transport.requests import Request

        credentials.refresh(Request())
    except Exception as exc:
        return {"ok": False, "error": _credentials_error(exc)}

    try:
        response = _count_tokens(
            f"https://{regional_host(location)}/v1/projects/{project}/locations/{location}"
            f"/publishers/google/models/{PROBE_MODEL}:countTokens",
            {"Authorization": f"Bearer {credentials.token}"},
            timeout,
        )
    except Exception as exc:
        return {"ok": False, "error": f"Couldn't reach Vertex AI ({exc.__class__.__name__})."}
    if response.status_code < 300:
        return {"ok": True}
    if response.status_code in (401, 403):
        return {"ok": False, "error": "Credentials work but lack Vertex AI access in this project."}
    if response.status_code == 404:
        return {"ok": False, "error": "Project or location not found on Vertex AI."}
    return {"ok": False, "error": f"Vertex AI returned HTTP {response.status_code}."}
