"""nova_dpapi.py - Windows DPAPI (CryptProtectData) encryption helper for Glass Nova local JSON state.

Uses standard-library ctypes against Windows crypt32.dll so local memories, tasks,
permission audit logs, workflows, and knowledge indexes are encrypted at rest under
the current Windows user account with zero third-party dependencies.

Legacy plaintext JSON files are read transparently and upgraded to DPAPI on next save.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
from typing import Any
import uuid

_CRYPTPROTECT_UI_FORBIDDEN = 0x01
_ENVELOPE_KEY = "_nova_dpapi"
_ENVELOPE_VERSION = 1


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def is_dpapi_available() -> bool:
    """Return True when Windows DPAPI encryption is active."""
    if os.environ.get("NOVA_DISABLE_DPAPI", "").strip() == "1":
        return False
    return sys.platform == "win32"


def protect_bytes(raw: bytes) -> bytes:
    """Encrypt bytes with Windows DPAPI scoped to the current Windows user."""
    if not is_dpapi_available():
        return raw
    if not raw:
        return b""
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(raw, len(raw))
    blob_in = _DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    blob_out = _DATA_BLOB()
    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        "GlassNovaLocalState",
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError(f"DPAPI CryptProtectData failed (error {ctypes.get_last_error()})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def unprotect_bytes(blob: bytes) -> bytes:
    """Decrypt bytes previously encrypted by protect_bytes()."""
    if not blob:
        return b""
    if sys.platform != "win32":
        raise OSError("Cannot decrypt Windows DPAPI data on a non-Windows platform.")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(blob, len(blob))
    blob_in = _DATA_BLOB(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    blob_out = _DATA_BLOB()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError(f"DPAPI CryptUnprotectData failed (error {ctypes.get_last_error()})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def dump_protected_json(path: str | Path, data: Any, indent: int = 2) -> None:
    """Atomically write JSON to disk, wrapped in a Windows DPAPI envelope when available."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    raw_json = json.dumps(data, ensure_ascii=False, indent=indent).encode("utf-8")

    if is_dpapi_available():
        encrypted = protect_bytes(raw_json)
        payload = json.dumps(
            {_ENVELOPE_KEY: _ENVELOPE_VERSION, "b64": base64.b64encode(encrypted).decode("ascii")},
            ensure_ascii=True,
        )
    else:
        payload = raw_json.decode("utf-8")

    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def load_protected_json(path: str | Path) -> Any:
    """Load JSON from disk, transparently decrypting DPAPI envelopes or reading legacy plaintext."""
    target = Path(path)
    parsed = json.loads(target.read_text(encoding="utf-8"))
    if (
        isinstance(parsed, dict)
        and parsed.get(_ENVELOPE_KEY) == _ENVELOPE_VERSION
        and isinstance(parsed.get("b64"), str)
    ):
        cipher = base64.b64decode(parsed["b64"].encode("ascii"))
        plain = unprotect_bytes(cipher)
        return json.loads(plain.decode("utf-8"))
    return parsed
