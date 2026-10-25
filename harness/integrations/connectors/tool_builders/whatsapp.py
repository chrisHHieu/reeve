"""Integration tools: whatsapp (tách từ integration_tools.py, hành vi giữ nguyên)."""
from __future__ import annotations

from .. import calls
from ..toolkit import attach, function_schema
from ..profiles import default_profile
from ..headers import bearer_headers

from typing import Any, Callable, Optional


def whatsapp_tools(secrets: Any, *, roots: Optional[list[Any]] = None) -> list[Callable[..., Any]]:
    tools: list[Callable[..., Any]] = []
    def whatsapp_send_message(to: str, text: str) -> dict[str, Any]:
        profile, err = default_profile(secrets, "whatsapp", "access_token", "phone_number_id")
        if err:
            return err
        return calls.request(
            "POST",
            f"https://graph.facebook.com/v21.0/{profile['phone_number_id']}/messages",
            headers=bearer_headers(profile["access_token"]),
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": text[:4096]},
            },
        )

    whatsapp_send_message.__name__ = "whatsapp_send_message"
    tools.append(
        attach(
            whatsapp_send_message,
            function_schema(
                "whatsapp_send_message",
                "Send a WhatsApp text message. Only delivered if the recipient messaged "
                "this number within the last 24 hours; otherwise use "
                "whatsapp_send_template. Requires user approval.",
                {"to": {"type": "string"}, "text": {"type": "string"}},
                ["to", "text"],
            ),
            approval=True,
            caps=["whatsapp", "write"],
        )
    )

    def whatsapp_send_template(
        to: str, template_name: str, language_code: str = "en_US"
    ) -> dict[str, Any]:
        profile, err = default_profile(secrets, "whatsapp", "access_token", "phone_number_id")
        if err:
            return err
        return calls.request(
            "POST",
            f"https://graph.facebook.com/v21.0/{profile['phone_number_id']}/messages",
            headers=bearer_headers(profile["access_token"]),
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": language_code},
                },
            },
        )

    whatsapp_send_template.__name__ = "whatsapp_send_template"
    tools.append(
        attach(
            whatsapp_send_template,
            function_schema(
                "whatsapp_send_template",
                "Send a pre-approved WhatsApp template message (works outside the "
                "24-hour service window). Requires user approval.",
                {
                    "to": {"type": "string"},
                    "template_name": {"type": "string"},
                    "language_code": {"type": "string"},
                },
                ["to", "template_name"],
            ),
            approval=True,
            caps=["whatsapp", "write"],
        )
    )

    # -- notion (managed OAuth or integration token, multi-workspace) --
    return tools
