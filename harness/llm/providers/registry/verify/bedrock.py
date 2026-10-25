"""The Test button for AWS Bedrock."""

from __future__ import annotations

import os
from typing import Any, Optional

from ...bedrock.credentials import session_kwargs


def verify_bedrock(fields: dict[str, Any], timeout: float) -> dict[str, Any]:
    """One cheap read-only call (list models), made with the same credential resolution the
    provider uses: explicit keys, then a profile, then the ambient chain."""

    def get(key: str) -> Optional[str]:
        return (fields.get(key) or "").strip() or None

    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        return {"ok": False, "error": "boto3 is not installed — `pip install 'reeve[bedrock]'`."}

    # Exactly one method is exercised, the one the form has selected, and the fields it needs
    # are checked here so the button says what is missing.
    method = get("auth_method") or "api_key"
    if method == "api_key" and not (get("bedrock_api_key") or os.environ.get("AWS_BEARER_TOKEN_BEDROCK")):
        return {"ok": False, "error": "Enter a Bedrock API key to test."}
    if method == "iam" and not (get("aws_access_key_id") and get("aws_secret_access_key")):
        return {"ok": False, "error": "Enter an access key ID and secret access key."}

    try:
        if method == "api_key":
            # boto3's only bearer channel is the environment variable, and a bearer token wins
            # over any ambient signing credentials for Bedrock calls.
            if get("bedrock_api_key"):
                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = get("bedrock_api_key")
            session_options: dict[str, Any] = {}
        elif method == "profile":
            # A blank profile means the default chain: environment, ~/.aws, an attached role.
            session_options = session_kwargs(get("aws_profile"), None, None, None)
        else:
            session_options = session_kwargs(
                None, get("aws_access_key_id"), get("aws_secret_access_key"), get("aws_session_token")
            )
        client = boto3.session.Session(**session_options).client(
            "bedrock",
            region_name=get("region"),
            config=Config(connect_timeout=timeout, read_timeout=timeout),
        )
        client.list_foundation_models()
    except Exception as exc:
        return {"ok": False, "error": _explain(exc)}
    return {"ok": True}


def _explain(exc: Exception) -> str:
    kind = exc.__class__.__name__
    if kind == "NoCredentialsError":
        return (
            "No AWS credentials found — enter keys or a profile, or run "
            "`aws configure` / `aws sso login` first."
        )
    if kind == "ProfileNotFound":
        return f"{exc}"
    # botocore raises modeled subclasses of ClientError, whose class name is the error code
    # ("AccessDeniedException") and not "ClientError", so the response shape is what identifies
    # them. Matching the class name sent every modeled error to the generic message.
    code = (getattr(exc, "response", None) or {}).get("Error", {}).get("Code", "")
    if code in ("UnrecognizedClientException", "InvalidSignatureException"):
        return "AWS rejected the credentials."
    if code in ("AccessDeniedException", "AccessDenied"):
        return (
            "Credentials work but lack Bedrock access "
            "(bedrock:ListFoundationModels) — check the key's policy, its "
            "expiry (short-term keys last up to 12h), and that the region "
            "matches where the key was created."
        )
    if code == "ExpiredTokenException":
        return "The credentials have expired — generate a new key."
    if code:
        return f"AWS Bedrock returned {code}."
    return f"Couldn't reach AWS Bedrock ({kind})."
