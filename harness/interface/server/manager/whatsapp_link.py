"""WhatsAppLinkMixin: the personal-WhatsApp daemon's life beside the server, and pairing.

The daemon holds the account's one connection (`integrations/whatsapp/daemon.py`). It starts with
the server once an account is linked, and on demand when the Connectors page starts a pairing;
it stops with the server, and it is told to unlink when the connector is disconnected.
"""

from __future__ import annotations

import logging
import os
import secrets as _secrets
import sys
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: How long the page waits for a freshly started daemon to say where it listens.
START_WAIT_S = 20.0


def _kill_tree(proc: Any) -> None:
    """The daemon and its interpreter: on Windows a venv's `python.exe` is a launcher whose child
    is the real process, and terminating the launcher alone left the daemon running."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            from harness.platform.spawn import run

            run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
        else:
            proc.terminate()
    except Exception:
        proc.kill()


class WhatsAppLinkMixin:
    _whatsapp_proc: Optional[Any] = None
    _whatsapp_nonce: str = ""

    def _whatsapp_allowed(self) -> Optional[str]:
        from harness.integrations.whatsapp.connector import available

        if not available():
            return "WhatsApp (personal) needs the `whatsapp` extra (neonize), which this install lacks"
        return None

    def _whatsapp_linked(self) -> bool:
        from harness.integrations.whatsapp.connector import NAME

        return bool((self.secrets.get(f"{NAME}:default") or {}).get("account"))

    def _whatsapp_daemon(self) -> None:
        """Start the daemon unless this server's own one is running and answering."""
        from harness.integrations.whatsapp import client
        from harness.integrations.whatsapp.connector import daemon_command
        from harness.integrations.whatsapp.daemon import INFO_FILE
        from harness.integrations.whatsapp.tools import account_folder
        from harness.platform.spawn import spawn

        proc = self._whatsapp_proc
        found = client.info()
        if proc is not None and proc.poll() is None and found and found.get("nonce") == self._whatsapp_nonce:
            return
        _kill_tree(proc)
        folder = account_folder()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            (folder / INFO_FILE).unlink()  # a file left by a daemon that died is not this one
        except OSError:
            pass
        self._whatsapp_nonce = _secrets.token_hex(8)
        self._whatsapp_proc = spawn(daemon_command(os.getpid(), self._whatsapp_nonce), cwd=str(folder))
        deadline = time.monotonic() + START_WAIT_S
        while time.monotonic() < deadline:
            found = client.info()
            if found and found.get("nonce") == self._whatsapp_nonce:
                return
            if self._whatsapp_proc.poll() is not None:
                raise RuntimeError("WhatsApp stopped while starting; see the server log")
            time.sleep(0.25)
        raise RuntimeError("WhatsApp did not start in time")

    def start_whatsapp(self) -> bool:
        """At server start: run the daemon when an account is linked. Returns whether it runs."""
        if not self._whatsapp_linked() or self._whatsapp_allowed():
            return False
        try:
            self._whatsapp_daemon()
        except Exception:
            logger.exception("WhatsApp (personal) did not start")
            return False
        return True

    def stop_whatsapp(self) -> None:
        proc, self._whatsapp_proc = self._whatsapp_proc, None
        _kill_tree(proc)

    def whatsapp_pair(self, phone: str = "", acknowledged: bool = False) -> dict[str, Any]:
        """Begin linking: the daemon runs and offers a QR code, or a code for `phone`."""
        from harness.integrations.whatsapp import client
        from harness.integrations.whatsapp.connector import RISK_NOTICE

        refused = self._whatsapp_allowed()
        if refused:
            return {"ok": False, "error": refused}
        if not acknowledged:
            return {"ok": False, "error": "risk acknowledgment required", "risk_notice": RISK_NOTICE}
        try:
            self._whatsapp_daemon()
            if phone.strip():
                answer = client.request("POST", "/pair", {"phone": phone})
                if answer.get("error"):
                    return {"ok": False, "error": answer["error"]}
                return {"ok": True, "code": answer.get("code", "")}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def whatsapp_pair_status(self) -> dict[str, Any]:
        """Where pairing stands; the first time the phone has linked, the connector is recorded."""
        from harness.integrations.whatsapp import client
        from harness.integrations.whatsapp.connector import record_paired

        try:
            status = client.request("GET", "/status", timeout=10.0)
        except Exception as exc:
            return {"running": False, "error": str(exc)}
        if status.get("paired") and not self._whatsapp_linked():
            record_paired(self.secrets, status.get("number", ""))
        shown = {k: status.get(k) for k in ("paired", "connected", "number", "history_chunks") if k in status}
        if status.get("qr"):
            # Drawn here, so the page needs no QR library: the phone scans an image, and the
            # string itself never has to reach the page.
            try:
                import segno

                shown["qr_svg"] = segno.make(status["qr"], error="m").svg_data_uri(scale=5, border=2)
            except Exception:
                shown["qr_error"] = "the QR code could not be drawn; link with a phone number instead"
        return {"running": True, **shown}

    # -- what the device keeps (the connector's page) -------------------------------------

    def whatsapp_kept(self) -> dict[str, Any]:
        """How much the linked device keeps on this computer, and for how long it keeps it."""
        from harness.integrations.whatsapp.store import Store, keep_days
        from harness.integrations.whatsapp.tools import account_folder

        folder = account_folder()
        shown: dict[str, Any] = {"messages": 0, "chats": 0, "oldest": None, "bytes": 0}
        if (folder / "messages.db").exists():
            store = Store(folder / "messages.db")
            try:
                shown = store.stats()
            finally:
                store.close()
        return {**shown, "keep_days": keep_days(folder), "folder": str(folder)}

    def whatsapp_set_keep(self, days: Optional[int]) -> dict[str, Any]:
        """A new keep choice: saved for the daemon's next pass, and applied now."""
        from harness.integrations.whatsapp.store import Store, set_keep_days
        from harness.integrations.whatsapp.tools import account_folder

        folder = account_folder()
        try:
            set_keep_days(folder, days)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if (folder / "messages.db").exists():
            store = Store(folder / "messages.db")
            try:
                store.keep_for(days, compact=True)
            finally:
                store.close()
        return {"ok": True, **self.whatsapp_kept()}

    def whatsapp_clear_kept(self) -> dict[str, Any]:
        """Every kept message goes; the device stays linked and keeps new ones."""
        from harness.integrations.whatsapp.store import Store
        from harness.integrations.whatsapp.tools import account_folder

        db = account_folder() / "messages.db"
        if db.exists():
            store = Store(db)
            try:
                store.forget_messages()
            finally:
                store.close()
        return {"ok": True, **self.whatsapp_kept()}

    def whatsapp_unlink(self) -> None:
        """Disconnecting unlinks the device from the phone and deletes what was kept here."""
        from harness.integrations.whatsapp import client
        from harness.integrations.whatsapp.connector import forget
        from harness.integrations.whatsapp.tools import account_folder

        try:
            client.request("POST", "/logout", timeout=15.0)
        except Exception:
            logger.info("WhatsApp was not running to unlink; the phone keeps the device until removed there")
        proc = self._whatsapp_proc
        if proc is not None:
            try:
                proc.wait(timeout=10)
            except Exception:
                _kill_tree(proc)
        self._whatsapp_proc = None
        forget(account_folder())
