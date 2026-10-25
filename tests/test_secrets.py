"""The SecretStore (C0): profiles kept and read back, `${NAME}` taken from the environment or a
`.env`, a status that shows no secret, a file only its owner can read, and a write that rides out a
moment when Windows will not let it replace the file.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time

import pytest

import harness.safety.secrets.private_files as secrets_mod
from harness.safety.secrets import RETIRED_PROFILES, SecretStore, drop_retired_profiles


@pytest.fixture
def store(tmp_path):
    return SecretStore(tmp_path / "secrets.json")


def _replace_failing(monkeypatch, times: int) -> list[int]:
    """`os.replace` refused `times` times (for ever if negative), then done; each try is counted."""
    tries: list[int] = []
    replace = os.replace

    def refusing(src, dst):
        tries.append(1)
        if times < 0 or len(tries) <= times:
            raise PermissionError(5, "Access is denied")
        return replace(src, dst)

    monkeypatch.setattr(secrets_mod.os, "replace", refusing)
    return tries


def test_a_profile_is_read_back_as_written_and_a_missing_one_is_none(store):
    store.put("slack:default", {"type": "token", "bot_token": "xoxb-123"})
    assert store.get("slack:default") == {"type": "token", "bot_token": "xoxb-123"}
    assert store.get("missing") is None


def test_deleting_says_whether_there_was_anything(store):
    store.put("x", {"a": 1})
    assert store.delete("x") is True and store.delete("x") is False and store.get("x") is None


def test_a_retired_profile_is_removed_and_the_rest_is_kept(store):
    store.put("decision:key", {"api_key": "invented", "provider": "typesafe"})
    store.put("provider:openai", {"api_key": "also-invented"})
    assert "decision:key" in RETIRED_PROFILES
    assert drop_retired_profiles(store) == ["decision:key"]
    assert store.get("decision:key") is None and store.get("provider:openai") == {"api_key": "also-invented"}
    assert drop_retired_profiles(store) == []  # nothing left to remove

    class _Locked:
        def delete(self, profile):
            raise OSError("locked")

    assert drop_retired_profiles(_Locked()) == []  # and a store that cannot answer costs nothing


def test_a_reference_is_filled_from_the_environment_or_a_dotenv_and_otherwise_left(store, tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TOK", "from-env")
    (tmp_path / ".env").write_text('DOCS_TOKEN = "shhh"\n', encoding="utf-8")
    store.put("slack:default", {"type": "token", "bot_token": "${MY_TOK}"})
    store.put("docs:default", {"headers": {"Authorization": "Bearer ${DOCS_TOKEN}"}})
    store.put("x", {"v": "${NOPE_NOT_SET}"})
    assert store.get("slack:default")["bot_token"] == "from-env"
    assert store.get("docs:default")["headers"]["Authorization"] == "Bearer shhh"
    assert store.get("x")["v"] == "${NOPE_NOT_SET}"


def test_the_status_says_what_each_profile_is_and_never_its_secret(store):
    store.put("gmail:default", {"type": "oauth", "access": "secret", "account_id": "me@x.com", "expires": time.time() - 10})
    store.put("slack:default", {"type": "token", "bot_token": "xoxb"})
    rows = {row["profile"]: row for row in store.status()}
    assert (rows["gmail:default"]["type"], rows["gmail:default"]["account"], rows["gmail:default"]["expired"]) == ("oauth", "me@x.com", True)
    assert rows["slack:default"]["expired"] is False
    shown = str(store.status())
    assert "secret" not in shown and "xoxb" not in shown


def test_secrets_file_is_restricted(tmp_path):
    """Only the current user may read the file. POSIX says so with mode 0600; Windows has no such
    bits, so there the ACL is checked: inheritance taken off and only the user granted."""
    path = tmp_path / "secrets.json"
    SecretStore(path).put("x", {"a": 1})
    if sys.platform == "win32":
        acl = subprocess.run(["icacls", str(path)], capture_output=True, text=True).stdout
        user = os.environ.get("USERNAME", "")
        assert user and user in acl
        assert "NT AUTHORITY\\SYSTEM" not in acl and "BUILTIN\\Administrators" not in acl  # gone with /inheritance:r
    else:
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_a_transient_replace_failure_is_retried(store, monkeypatch):
    """Windows can refuse to rename over a file that is open (another process reading it, a scanner
    holding the new temp). A real session lost a calendar call to `[WinError 5] Access is denied:
    '.secrets.json.…tmp' -> 'secrets.json'`; both holders let go soon, so the write waits them out."""
    tries = _replace_failing(monkeypatch, 2)
    store.put("x", {"a": 1})
    assert len(tries) == 3 and store.get("x") == {"a": 1}


def test_a_persistent_replace_failure_still_raises(store, tmp_path, monkeypatch):
    tries = _replace_failing(monkeypatch, -1)
    with pytest.raises(PermissionError):
        store.put("x", {"a": 1})
    assert len(tries) == 5  # it waits a bounded number of times
    assert list(tmp_path.glob("*.tmp")) == []
