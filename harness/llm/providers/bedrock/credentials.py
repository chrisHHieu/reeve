"""Which AWS credentials a Bedrock client is built from.

One authentication method is in force at a time, chosen by the profile's `auth_method`:

- `api_key`: a Bedrock API key (a bearer token from the console). It travels in
  `AWS_BEARER_TOKEN_BEDROCK`, which boto3 prefers over request signing for Bedrock.
- `profile`: a named `~/.aws` profile (this covers `aws sso login`). Blank means the default
  credential chain: environment, `~/.aws`, an attached role.
- `iam`: explicit access keys, with an optional STS session token.

Values stored for a method that is not selected are dropped before they reach a client, so a
stale value can never leak into another path (the Anthropic SDK refuses a mix outright). With
no method, or an unknown one, whatever fields are present are used, the API key first.
"""

from __future__ import annotations

from typing import Any, Optional

Credentials = tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]


def narrow_to_method(
    auth_method: Optional[str],
    bedrock_api_key: Optional[str],
    profile_name: Optional[str],
    access_key_id: Optional[str],
    secret_access_key: Optional[str],
    session_token: Optional[str],
) -> Credentials:
    """(api key, profile, access key id, secret key, session token) with every field that
    belongs to another method cleared."""
    if auth_method == "api_key":
        return bedrock_api_key, None, None, None, None
    if auth_method == "profile":
        return None, profile_name, None, None, None
    if auth_method == "iam":
        return None, None, access_key_id, secret_access_key, session_token
    return bedrock_api_key, profile_name, access_key_id, secret_access_key, session_token


def session_kwargs(
    profile_name: Optional[str],
    access_key_id: Optional[str],
    secret_access_key: Optional[str],
    session_token: Optional[str],
) -> dict[str, Any]:
    """Arguments for `boto3.Session`: explicit keys first, then a named profile, else nothing
    (the ambient chain)."""
    if access_key_id and secret_access_key:
        kwargs: dict[str, Any] = {
            "aws_access_key_id": access_key_id,
            "aws_secret_access_key": secret_access_key,
        }
        if session_token:
            kwargs["aws_session_token"] = session_token
        return kwargs
    if profile_name:
        return {"profile_name": profile_name}
    return {}
