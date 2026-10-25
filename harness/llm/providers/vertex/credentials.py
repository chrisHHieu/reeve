"""Google Cloud credentials for Vertex AI.

One authentication method is in force at a time, chosen by the profile's `auth_method`:

- `adc`: Application Default Credentials (`gcloud auth application-default login`), Google's
  recommended path. Nothing is stored.
- `service_account`: an explicit service-account JSON, pasted or as a file path.
- `api_key`: a Vertex API key (express mode). Gemini only: the genai SDK takes it, and it
  excludes project and location, but Claude on Vertex and the open-weight endpoint need OAuth
  credentials, so those families refuse it with a message pointing at the other methods.

Values stored for a method that is not selected are dropped, so a stale one never reaches
another path. With no method, or an unknown one, whatever is present is used: the service
account, else ADC.
"""

from __future__ import annotations

import json
from typing import Any, Optional

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def regional_host(location: Optional[str]) -> str:
    """The Vertex REST host for a location. `global`, used by newer Gemini models, has no
    region prefix."""
    if not location or location == "global":
        return "aiplatform.googleapis.com"
    return f"{location}-aiplatform.googleapis.com"


def load_credentials(service_account_json: Optional[str]) -> Any:
    """Credentials from a service-account JSON (its content or a path to it). Blank gives None,
    which leaves the SDKs, and the token path, on Application Default Credentials."""
    raw = (service_account_json or "").strip()
    if not raw:
        return None
    from google.oauth2 import service_account

    if raw.startswith("{"):
        return service_account.Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    return service_account.Credentials.from_service_account_file(raw, scopes=SCOPES)


def narrow_to_method(
    auth_method: Optional[str], service_account_json: Optional[str], api_key: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """(service-account JSON, API key) with whatever belongs to another method cleared."""
    if auth_method == "adc":
        return None, None
    if auth_method == "service_account":
        return service_account_json, None
    if auth_method == "api_key":
        return None, api_key
    return service_account_json, api_key
