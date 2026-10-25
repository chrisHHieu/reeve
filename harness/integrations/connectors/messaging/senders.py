"""One-shot outbound sends: a single HTTP POST, no SDK and no live connection.

They power the `send_message` tool and the agent's replies. Telegram and Slack both take a plain
HTTP call for sending, so a synchronous `httpx` client does it without the heavy SDKs (only the
inbound listeners need those), and sync matches the tool registry's `execute` contract, which the
loop runs in a thread.

A `Sender` is `(token, chat_id, text, thread_id) -> SendResult`. The tables at the bottom can be
swapped, so tests inject fakes and nothing touches the network.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from .events import SendResult

Sender = Callable[[str, str, str, Optional[str]], SendResult]
#: (token, chat_id, thread_id, filename, data, title, comment) -> SendResult
FileSender = Callable[[str, str, Optional[str], str, bytes, Optional[str], Optional[str]], SendResult]

_TIMEOUT = 30.0


def slack_api_base() -> str:
    """The Web API root, with its trailing slash. `SLACK_API_URL` points sends at a local fake
    for tests."""
    return os.environ.get("SLACK_API_URL", "https://slack.com/api/")


def send_telegram(token: str, chat_id: str, text: str, thread_id: Optional[str] = None) -> SendResult:
    import httpx

    payload: dict = {"chat_id": chat_id, "text": text}
    # The General forum topic is thread "1", which sendMessage refuses, so it is left out.
    if thread_id and thread_id != "1":
        try:
            payload["message_thread_id"] = int(thread_id)
        except ValueError:
            pass
    try:
        data = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=_TIMEOUT
        ).json()
    except Exception as exc:
        return SendResult(False, error=str(exc))
    if data.get("ok"):
        return SendResult(True, message_id=str(data.get("result", {}).get("message_id")))
    return SendResult(False, error=data.get("description") or "telegram send failed")


def _post_to_slack(token: str, payload: dict) -> SendResult:
    import httpx

    try:
        data = httpx.post(
            f"{slack_api_base()}chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=_TIMEOUT,
        ).json()
    except Exception as exc:
        return SendResult(False, error=str(exc))
    if data.get("ok"):
        return SendResult(True, message_id=data.get("ts"))
    # The raw error code travels. `failures.py` turns it into the next move at the tool
    # boundary, where both readers are waiting: the model deciding what to do and the person
    # reading the result card. The interactive send answers the gateway and not a tool result,
    # so its errors reach neither reader.
    return SendResult(False, error=data.get("error") or "slack send failed")


def send_slack(token: str, chat_id: str, text: str, thread_id: Optional[str] = None) -> SendResult:
    payload: dict = {"channel": chat_id, "text": text}
    if thread_id:
        payload["thread_ts"] = thread_id
    return _post_to_slack(token, payload)


def send_slack_file(
    token: str,
    chat_id: str,
    thread_id: Optional[str],
    filename: str,
    data: bytes,
    title: Optional[str] = None,
    comment: Optional[str] = None,
) -> SendResult:
    """Upload through `files_upload_v2`, the only path that is not deprecated: reserve an upload
    URL, PUT the bytes, then complete the upload into the channel or thread. Slack draws its own
    preview of a PDF, CSV or image, which is the reason to send the file and not a thumbnail."""
    import httpx

    headers = {"Authorization": f"Bearer {token}"}
    try:
        reserved = httpx.post(
            f"{slack_api_base()}files.getUploadURLExternal",
            headers=headers,
            data={"filename": filename, "length": str(len(data))},
            timeout=_TIMEOUT,
        ).json()
        if not reserved.get("ok"):
            return SendResult(False, error=reserved.get("error") or "slack upload-url failed")
        uploaded = httpx.post(
            reserved["upload_url"], files={"file": (filename, data)}, timeout=max(_TIMEOUT, 120.0)
        )
        if uploaded.status_code != 200:
            return SendResult(False, error=f"slack upload failed ({uploaded.status_code})")
        completion: dict = {
            "files": [{"id": reserved["file_id"], "title": title or filename}],
            "channel_id": chat_id,
        }
        if thread_id:
            completion["thread_ts"] = thread_id
        if comment:
            completion["initial_comment"] = comment
        outcome = httpx.post(
            f"{slack_api_base()}files.completeUploadExternal",
            headers=headers,
            json=completion,
            timeout=_TIMEOUT,
        ).json()
    except Exception as exc:
        return SendResult(False, error=str(exc))
    if outcome.get("ok"):
        return SendResult(True, message_id=reserved["file_id"])
    return SendResult(False, error=outcome.get("error") or "slack file send failed")


DEFAULT_SENDERS: dict[str, Sender] = {"telegram": send_telegram, "slack": send_slack}
DEFAULT_FILE_SENDERS: dict[str, FileSender] = {"slack": send_slack_file}
