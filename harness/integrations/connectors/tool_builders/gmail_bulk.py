"""Gmail: label upkeep and bulk label changes (rename, delete, batchModify)."""
from __future__ import annotations

from ..toolkit import attach, function_schema
from ..profiles import MAILBOX_ACCOUNT_PROP

from typing import Any, Callable

from ..gmail_message import call_factory


def gmail_bulk_tools(secrets: Any) -> list[Callable[..., Any]]:
    tools: list[Callable[..., Any]] = []
    call = call_factory(secrets)

    def register(fn, name, description, props, required, *, approval=False):
        fn.__name__ = name
        props = {**props, "account": MAILBOX_ACCOUNT_PROP}
        tools.append(attach(fn, function_schema(name, description, props, required), approval=approval, caps=["gmail", "write"]))


    def gmail_update_label(label_id: str, name: str, account: str = ""):
        return call(account, "PATCH", f"labels/{label_id}", body={"name": name})

    register(
        gmail_update_label,
        "gmail_update_label",
        "Rename a label. Its id, and the messages carrying it, stay as they are.",
        {"label_id": {"type": "string"}, "name": {"type": "string"}},
        ["label_id", "name"],
    )

    def gmail_delete_label(label_id: str, account: str = ""):
        return call(account, "DELETE", f"labels/{label_id}")

    register(
        gmail_delete_label,
        "gmail_delete_label",
        "Delete a label. The messages stay; they lose the label. Requires approval.",
        {"label_id": {"type": "string"}},
        ["label_id"],
        approval=True,
    )

    def gmail_batch_modify(message_ids: str, add_label_ids: str = "", remove_label_ids: str = "", account: str = ""):
        ids = [i.strip() for i in str(message_ids).split(",") if i.strip()]
        add = [i.strip() for i in str(add_label_ids).split(",") if i.strip()]
        remove = [i.strip() for i in str(remove_label_ids).split(",") if i.strip()]
        if not ids or not (add or remove):
            return {"error": "give at least one message id and one label to add or remove"}
        if len(ids) > 1000:
            return {"error": "at most 1000 messages per call"}
        return call(
            account,
            "POST",
            "messages/batchModify",
            body={"ids": ids, **({"addLabelIds": add} if add else {}), **({"removeLabelIds": remove} if remove else {})},
        )

    register(
        gmail_batch_modify,
        "gmail_batch_modify",
        "Add and remove labels on many messages in one call (up to 1000): archive (remove INBOX), "
        "mark read (remove UNREAD), star (add STARRED) or apply a label to everything a search found.",
        {
            "message_ids": {"type": "string", "description": "Comma-separated"},
            "add_label_ids": {"type": "string"},
            "remove_label_ids": {"type": "string"},
        },
        ["message_ids"],
    )
    return tools
