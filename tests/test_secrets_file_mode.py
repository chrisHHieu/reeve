"""A secret is never on disk where others can read it, not even for a moment (#143).

The write used to make its temporary file with `Path.write_text` (the umask's mode, 0644 on most
machines), then chmod it to 0600, then rename it: for as long as the write took, the plaintext was
readable by every other process on the machine. The file is now made 0600 and empty, then written.
"""

import json
import stat
import sys

import pytest

from harness.safety.secrets import SecretStore, write_private_text
from harness.safety.secrets import private_files as secrets_mod

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits; Windows goes through icacls")


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def _store(tmp_path, **entries):
    store = SecretStore(tmp_path / "secrets.json")
    for name, key in (entries or {"openai": "sk-live"}).items():
        store.put(name, {"api_key": key})
    return store


@posix_only
def test_the_secrets_file_and_a_private_text_are_for_their_owner_only(tmp_path):
    _store(tmp_path, openai="sk-live-secret")
    assert _mode(tmp_path / "secrets.json") == 0o600
    assert _mode(write_private_text(tmp_path / "token.txt", "sk-live-secret")) == 0o600


@posix_only
def test_temp_file_is_never_group_or_world_readable_mid_write(tmp_path, monkeypatch):
    """The regression itself. Both the old writer and the new one call `restrict_to_user`; the mode
    of the temporary file is read at that moment. The old order had the plaintext at 0644 by then,
    the new one has an empty file at 0600."""
    seen: dict[str, int] = {}
    restrict = secrets_mod.restrict_to_user

    def watching(path, *, is_dir):
        if not is_dir and path.exists():
            seen[path.name] = _mode(path)
        return restrict(path, is_dir=is_dir)

    monkeypatch.setattr(secrets_mod, "restrict_to_user", watching)
    _store(tmp_path)
    assert seen, "the writer restricted no temporary file"
    for name, mode in seen.items():
        assert mode & (stat.S_IRGRP | stat.S_IROTH) == 0, f"{name} held the plaintext at {oct(mode)}"


def test_nothing_but_the_secrets_file_is_left(tmp_path):
    _store(tmp_path)
    assert [p.name for p in tmp_path.iterdir() if p.name != "secrets.json"] == []


def test_what_is_written_is_read_back(tmp_path):
    _store(tmp_path, openai="sk-live", anthropic="sk-ant")
    on_disk = json.loads((tmp_path / "secrets.json").read_text())
    assert (on_disk["openai"]["api_key"], on_disk["anthropic"]["api_key"]) == ("sk-live", "sk-ant")
    assert SecretStore(tmp_path / "secrets.json").get("openai")["api_key"] == "sk-live"


def test_a_write_that_fails_leaves_the_file_as_it_was_and_no_temp(tmp_path, monkeypatch):
    store = _store(tmp_path, openai="sk-original")

    def disk_full(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(secrets_mod.os, "replace", disk_full)
    with pytest.raises(OSError):
        store.put("openai", {"api_key": "sk-replacement"})
    assert json.loads((tmp_path / "secrets.json").read_text())["openai"]["api_key"] == "sk-original"
    assert [p.name for p in tmp_path.iterdir()] == ["secrets.json"]


def test_a_link_planted_at_a_guessable_temp_name_does_not_redirect_the_write(tmp_path):
    # the old temporary file was always `<name>.tmp`, so a link there sent the write elsewhere
    victim = tmp_path / "victim.txt"
    victim.write_text("do not clobber")
    try:
        (tmp_path / "secrets.json.tmp").symlink_to(victim)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    _store(tmp_path)
    assert victim.read_text() == "do not clobber"
