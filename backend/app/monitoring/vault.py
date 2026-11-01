"""Protected storage for the Google refresh token.

The refresh token is a long-lived credential, so it is never stored in plain text.
``DpapiVault`` seals it with Windows DPAPI (``CryptProtectData``) through
``ctypes``: only the same Windows user on the same machine can open it, and the
database column (and any database backup) holds ciphertext that is useless
elsewhere. No dependency is needed and AURORA holds no key. Where DPAPI does not
exist (non-Windows), there is no vault: the token can then only come from the
``GOOGLE_REFRESH_TOKEN`` environment variable, and the integration says so.

Sealed values are base64 text. Nothing here logs or returns plain secrets.
"""

import base64
import ctypes
import sys
from typing import Any, Protocol

from app.monitoring.errors import GscStorageUnavailable

# Binds the blob to AURORA rather than to any program running as the same user.
ENTROPY = b"aurora-gsc-refresh-token-v1"
CRYPTPROTECT_UI_FORBIDDEN = 0x1


class TokenVault(Protocol):
    name: str

    def seal(self, secret: str) -> str: ...

    def open(self, sealed: str) -> str: ...


class _Blob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _blob(data: bytes) -> tuple[_Blob, Any]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


class DpapiVault:
    """Windows DPAPI (current-user scope)."""

    name = "dpapi"

    @staticmethod
    def available() -> bool:
        return sys.platform == "win32"

    def _call(self, data: bytes, *, protect: bool) -> bytes:
        windll = ctypes.windll  # type: ignore[attr-defined]  # only reached on Windows
        source, keep_source = _blob(data)
        entropy, keep_entropy = _blob(ENTROPY)
        out = _Blob()
        if protect:
            ok = windll.crypt32.CryptProtectData(
                ctypes.byref(source),
                "aurora",
                ctypes.byref(entropy),
                None,
                None,
                CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(out),
            )
        else:
            ok = windll.crypt32.CryptUnprotectData(
                ctypes.byref(source),
                None,
                ctypes.byref(entropy),
                None,
                None,
                CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(out),
            )
        if not ok:
            raise GscStorageUnavailable(
                "The stored Search Console credential could not be opened by this "
                "Windows account; connect Search Console again."
            )
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            windll.kernel32.LocalFree(out.pbData)
            del keep_source, keep_entropy

    def seal(self, secret: str) -> str:
        sealed = self._call(secret.encode("utf-8"), protect=True)
        return base64.b64encode(sealed).decode("ascii")

    def open(self, sealed: str) -> str:
        try:
            raw = base64.b64decode(sealed.encode("ascii"), validate=True)
        except ValueError as exc:
            raise GscStorageUnavailable("The stored credential is malformed.") from exc
        return self._call(raw, protect=False).decode("utf-8")


def select_vault(mode: str) -> TokenVault | None:
    """auto: DPAPI on Windows, else none; dpapi: required; environment: none."""
    if mode == "environment":
        return None
    if DpapiVault.available():
        return DpapiVault()
    if mode == "dpapi":
        raise GscStorageUnavailable("GSC_TOKEN_STORAGE=dpapi needs Windows.")
    return None
