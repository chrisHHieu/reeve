"""Routes for the connectors that hold several accounts: Gmail, Google Calendar and the rest."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI


def register_connector_accounts(app: FastAPI, manager) -> None:
    # Gmail and Calendar keep their own email-addressed routes; they are registered before the
    # generic ones so that their literal paths win.
    @app.post("/v1/connectors/gmail/accounts/{email}/disconnect")
    async def gmail_account_disconnect(email: str) -> dict[str, Any]:
        """Drop ONE mailbox; the default pointer moves to the next account."""
        from harness.integrations.connectors import gmail_accounts

        return gmail_accounts.disconnect_account(manager.secrets, email)

    @app.post("/v1/connectors/gmail/accounts/{email}/default")
    def gmail_account_default(email: str) -> dict[str, Any]:
        from harness.integrations.connectors import gmail_accounts

        return gmail_accounts.set_default(manager.secrets, email)

    @app.patch("/v1/connectors/gmail/filters")
    def gmail_filters(body: dict) -> dict[str, Any]:
        """Replace the "Never show agents" lists. They are enforced in the local tool layer: an
        agent sees silent omissions, and the person sees counts and an audit row."""
        from harness.integrations.connectors import gmail_accounts

        senders = body.get("senders") if isinstance(body, dict) else None
        labels = body.get("labels") if isinstance(body, dict) else None
        if senders is not None and not isinstance(senders, list):
            return {"ok": False, "error": "senders must be a list"}
        if labels is not None and not isinstance(labels, list):
            return {"ok": False, "error": "labels must be a list"}
        return gmail_accounts.set_filters(manager.secrets, senders, labels)

    @app.post("/v1/connectors/google_calendar/accounts/{email}/disconnect")
    async def gcal_account_disconnect(email: str) -> dict[str, Any]:
        """Drop ONE Google Calendar account; the default pointer moves to the next."""
        from harness.integrations.connectors import gcal_accounts

        return gcal_accounts.disconnect_account(manager.secrets, email)

    @app.post("/v1/connectors/google_calendar/accounts/{email}/default")
    def gcal_account_default(email: str) -> dict[str, Any]:
        from harness.integrations.connectors import gcal_accounts

        return gcal_accounts.set_default(manager.secrets, email)

    @app.post("/v1/connectors/{name}/accounts/{account_id}/disconnect")
    async def account_disconnect(name: str, account_id: str) -> dict[str, Any]:
        """The generic per-account disconnect of an account-patterned connector."""
        from harness.integrations.connectors import accounts

        if not accounts.is_account_connector(name):
            return {"ok": False, "error": "not a multi-account connector"}
        return accounts.disconnect_account(manager.secrets, name, account_id)

    @app.post("/v1/connectors/{name}/accounts/{account_id}/default")
    def account_default(name: str, account_id: str) -> dict[str, Any]:
        from harness.integrations.connectors import accounts

        if not accounts.is_account_connector(name):
            return {"ok": False, "error": "not a multi-account connector"}
        return accounts.set_default(manager.secrets, name, account_id)
