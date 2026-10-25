"""Model lists that need more than a bearer key: Bedrock, Vertex and the Codex sign-in."""

from __future__ import annotations

import os
from typing import Any, Optional

from .fetch import Listing, get_json, tidy

_VERTEX_PUBLISHER_MODELS = "publishers/google/models?pageSize=200"


def bedrock(fields: dict[str, Any], timeout: float) -> Listing:
    from ..bedrock.credentials import session_kwargs as aws_session_kwargs
    from ..registry.verify.bedrock import verify_bedrock

    # The Test button's probe already validates missing fields, resolves the credential and maps the
    # errors, so reuse it and pay for the listing only once the credential is known to work.
    probe = verify_bedrock(fields, timeout)
    if not probe.get("ok"):
        return None, str(probe.get("error") or "AWS rejected the credentials")

    def field(key: str) -> Optional[str]:
        return (fields.get(key) or "").strip() or None

    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        return None, "boto3 is not installed"

    method = field("auth_method") or "api_key"
    try:
        if method == "api_key":
            if field("bedrock_api_key"):
                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = field("bedrock_api_key")
            session_kwargs: dict[str, Any] = {}
        elif method == "profile":
            session_kwargs = aws_session_kwargs(field("aws_profile"), None, None, None)
        else:
            session_kwargs = aws_session_kwargs(
                None, field("aws_access_key_id"), field("aws_secret_access_key"), field("aws_session_token")
            )
        client = boto3.session.Session(**session_kwargs).client(
            "bedrock",
            region_name=field("region"),
            config=Config(connect_timeout=timeout, read_timeout=timeout),
        )
        info = client.list_foundation_models()
    except Exception as exc:
        code = (getattr(exc, "response", None) or {}).get("Error", {}).get("Code", "")
        return None, f"AWS Bedrock returned {code or exc.__class__.__name__}"

    ids = []
    for row in info.get("modelSummaries") or []:
        if not isinstance(row, dict):
            continue
        outputs = row.get("outputModalities")
        if isinstance(outputs, list) and "TEXT" not in outputs:
            continue
        ids.append(str(row.get("modelId") or ""))
    return tidy(ids), None


def _vertex_request(fields: dict[str, Any], project: str, location: str):
    """`(url, headers)` for the publisher list, or `(None, why not)`."""
    from ..vertex.credentials import load_credentials, regional_host

    method = (fields.get("auth_method") or "").strip() or (
        "service_account" if (fields.get("service_account_json") or "").strip() else "adc"
    )
    if method == "api_key":
        key = (fields.get("vertex_api_key") or "").strip()
        if not key:
            return None, "no Vertex API key stored"
        return f"https://aiplatform.googleapis.com/v1/{_VERTEX_PUBLISHER_MODELS}", {"x-goog-api-key": key}
    try:
        credentials = load_credentials(fields.get("service_account_json")) if method == "service_account" else None
        if credentials is None:
            import google.auth

            credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        from google.auth.transport.requests import Request

        credentials.refresh(Request())
    except Exception as exc:
        return None, f"couldn't load Google credentials ({exc.__class__.__name__})"
    url = f"https://{regional_host(location)}/v1/projects/{project}/locations/{location}/{_VERTEX_PUBLISHER_MODELS}"
    return url, {"Authorization": f"Bearer {credentials.token}"}


def vertex(fields: dict[str, Any], timeout: float) -> Listing:
    """Best-effort publisher listing for Vertex.

    The list endpoint answers 403/404 under plain user ADC (checked live 2026-07-26, see
    `registry/verify/vertex.py`), so express API keys and service accounts are the only paths that
    may answer. Anything else degrades to the curated fallback with the reason attached.
    """
    import httpx

    project = (fields.get("project") or "").strip()
    location = (fields.get("location") or "").strip()
    if not project or not location:
        return None, "project and location are required"
    url, headers = _vertex_request(fields, project, location)
    if url is None:
        return None, headers  # the reason
    try:
        response = httpx.get(url, headers=headers, timeout=timeout)
    except Exception as exc:
        return None, f"couldn't reach Vertex AI ({exc.__class__.__name__})"
    if response.status_code >= 300:
        return None, f"Vertex AI returned HTTP {response.status_code}"
    try:
        payload = response.json()
    except Exception:
        return None, "Vertex AI answered with a non-JSON body"
    names = (str(row.get("name") or "") for row in payload.get("models") or [] if isinstance(row, dict))
    return tidy(name.split("/")[-1] for name in names), None


def codex() -> Listing:
    """The ChatGPT sign-in's own catalog (`codex debug models`): that OAuth token is not an API key."""
    from harness.orchestration.subagent_external import _codex_cli_models

    models = _codex_cli_models()
    if not models:
        return None, "the Codex CLI catalog is unavailable (install/sign in to Codex)"
    return tidy(m.get("id") for m in models), None


def claude_subscription(fields: dict[str, Any], timeout: float) -> Listing:
    """The models Claude Subscription (OAuth) can see via `GET https://api.anthropic.com/v1/models`."""
    tokens = fields.get("tokens") if isinstance(fields.get("tokens"), dict) else {}
    access_token = str(tokens.get("access_token") or "").strip()
    if not access_token:
        # Fallback to loading from SecretStore if not passed in fields
        try:
            from harness.safety.secrets.store import SecretStore
            secrets = SecretStore()
            data = secrets.get("provider:anthropic-subscription")
            tokens_dict = (data.get("tokens") or {}) if isinstance(data, dict) else {}
            access_token = str(tokens_dict.get("access_token") or "").strip()
        except Exception:
            pass

    if not access_token:
        return None, "not signed in to Anthropic Subscription"

    payload, error = get_json(
        "https://api.anthropic.com/v1/models?limit=100",
        headers={
            "Authorization": f"Bearer {access_token}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "claude-cli/0.2.29",
        },
        timeout=timeout,
    )
    if payload is None:
        return None, error
    data_rows = payload.get("data") if isinstance(payload, dict) else []
    return tidy(str(m.get("id")) for m in data_rows if isinstance(m, dict) and m.get("id")), None

