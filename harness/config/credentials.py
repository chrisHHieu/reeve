"""Per-profile password custody for SSH remote environments (password auth).

The profile file is plain JSON with 0600 semantics and must never contain a password. When
a user checks "Remember password", the secret goes to the platform's own credential store
— on Windows, the Credential Manager, spoken to through ``ctypes`` (advapi32's
``CredWriteW``/``CredReadW``/``CredDeleteW``) so no new dependency rides — under a
per-profile target. The profile itself keeps only ``remembered: true``.

Everything goes through the small ``get``/``put``/``delete`` seam so tests never touch the
real Credential Manager. Platforms without one get ``UnavailableCredentialStore``: reads
answer "nothing remembered" and a write refuses with the same clear sentence the API
returns, so "Remember password" fails honestly instead of silently storing nothing.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any, NamedTuple, Optional, Protocol, runtime_checkable

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_ALREADY_EXISTS = 183
ERROR_NOT_FOUND = 1168

#: One Credential Manager entry per profile. The id is a UUID we generated, and the
#: prefix names the owner so a human scanning `cmdkey /list` can tell where it came from.
CREDENTIAL_TARGET_PREFIX = "reeve:remote-environment:"

UNAVAILABLE_MESSAGE = (
    "Remembering passwords requires Windows Credential Manager. Leave \"Remember "
    "password\" unchecked to use the password for this connection only."
)


class CredentialStoreUnavailable(ValueError):
    """The platform has no credential store, or the store refused the request.

    A ``ValueError`` on purpose: every caller that turns user input into an HTTP 400 or a
    typed session-start refusal already catches ``ValueError``, and the message is the
    person-readable half.
    """


def credential_target(environment_id: str) -> str:
    """The per-profile Credential Manager target name."""
    return f"{CREDENTIAL_TARGET_PREFIX}{environment_id}"


@runtime_checkable
class CredentialStore(Protocol):
    """The seam: read, write, and remove one secret by target name."""

    #: False for stores that cannot hold anything (non-Windows), so callers can skip
    #: cleanup instead of asking a store to delete what it never could have written.
    available: bool

    def get(self, target: str) -> Optional[str]:
        """The remembered secret, or None when nothing is stored under `target`."""
        ...

    def put(self, target: str, secret: str) -> None:
        """Store (or replace) the secret, or raise `CredentialStoreUnavailable`."""
        ...

    def delete(self, target: str) -> bool:
        """Remove the secret; False when there was nothing to remove."""
        ...


class UnavailableCredentialStore:
    """The default on platforms with no credential store (and a test double for the seam).

    Reads degrade to "nothing remembered" — a profile saved on Windows and opened on
    Linux still lists, and its ssh falls back to keys. A write is where the user asked
    for custody, so that is where the clear refusal lives.
    """

    available = False

    def get(self, target: str) -> Optional[str]:
        return None

    def put(self, target: str, secret: str) -> None:
        raise CredentialStoreUnavailable(UNAVAILABLE_MESSAGE)

    def delete(self, target: str) -> bool:
        return False


class _CredentialApi(NamedTuple):
    credential_type: Any
    byte_pointer: Any
    write: Any
    read: Any
    delete: Any
    free: Any


def _load_windows_api() -> _CredentialApi:
    """Bind the three advapi32 calls and the CREDENTIALW struct, once.

    Imported/built lazily (and only on Windows) because ``ctypes.wintypes`` does not
    import on every platform; the module itself stays importable everywhere.
    """
    from ctypes import wintypes

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL
    advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
    ]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi32.CredDeleteW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    advapi32.CredFree.restype = None
    return _CredentialApi(
        credential_type=CREDENTIALW,
        byte_pointer=ctypes.POINTER(ctypes.c_byte),
        write=advapi32.CredWriteW,
        read=advapi32.CredReadW,
        delete=advapi32.CredDeleteW,
        free=advapi32.CredFree,
    )


class WindowsCredentialStore:
    """Windows Credential Manager generic credentials, keyed by target name.

    Generic credentials are the right kind here: they are per-user, app-defined strings
    (no domain/SID machinery), visible in the Credential Manager UI, removable by the
    user at any time, and scoped to the logged-in user by ``CRED_PERSIST_LOCAL_MACHINE``.
    """

    available = True

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise CredentialStoreUnavailable(UNAVAILABLE_MESSAGE)
        self._api = _load_windows_api()

    def get(self, target: str) -> Optional[str]:
        pointer = ctypes.POINTER(self._api.credential_type)()
        if not self._api.read(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            error = ctypes.get_last_error()
            if error == ERROR_NOT_FOUND:
                return None
            raise CredentialStoreUnavailable(
                f"Windows Credential Manager could not read the remembered password "
                f"(error {error})."
            )
        try:
            credential = pointer.contents
            raw = ctypes.string_at(
                credential.CredentialBlob, credential.CredentialBlobSize
            )
        finally:
            self._api.free(pointer)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raise CredentialStoreUnavailable(
                "The remembered password is not valid UTF-8; re-enter and save it."
            ) from None

    def put(self, target: str, secret: str) -> None:
        api = self._api
        blob = secret.encode("utf-8")
        buffer = ctypes.create_string_buffer(blob)
        credential = api.credential_type()
        credential.Type = CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(buffer, api.byte_pointer)
        credential.Persist = CRED_PERSIST_LOCAL_MACHINE
        if api.write(ctypes.byref(credential), 0):
            return
        error = ctypes.get_last_error()
        if error == ERROR_ALREADY_EXISTS:
            # Some builds refuse to overwrite; remove and retry. A concurrent holder is
            # the user's own credential manager, so a failed delete surfaces as-is.
            self.delete(target)
            if api.write(ctypes.byref(credential), 0):
                return
            error = ctypes.get_last_error()
        raise CredentialStoreUnavailable(
            f"Windows Credential Manager could not save the password (error {error})."
        )

    def delete(self, target: str) -> bool:
        if self._api.delete(target, CRED_TYPE_GENERIC, 0):
            return True
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            return False
        raise CredentialStoreUnavailable(
            f"Windows Credential Manager could not remove the remembered password "
            f"(error {error})."
        )


def default_credential_store() -> CredentialStore:
    """The real store on Windows; the honest refusal everywhere else."""
    if sys.platform == "win32":
        return WindowsCredentialStore()
    return UnavailableCredentialStore()


__all__ = [
    "CREDENTIAL_TARGET_PREFIX",
    "CRED_TYPE_GENERIC",
    "CRED_PERSIST_LOCAL_MACHINE",
    "CredentialStore",
    "CredentialStoreUnavailable",
    "UNAVAILABLE_MESSAGE",
    "UnavailableCredentialStore",
    "WindowsCredentialStore",
    "credential_target",
    "default_credential_store",
]
