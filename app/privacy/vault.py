"""Placeholder vault: the placeholder<->value mapping for one session.

Same value -> same placeholder within a session. The mapping never leaves
the VPS; it can be sealed (Fernet: AES-128-CBC + HMAC) for storage between
turns. The key comes from HUB_PRIVACY_KEY (a Fernet key in .env), never code.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


class VaultKeyError(Exception):
    pass


def _fernet(key: Optional[str]) -> Fernet:
    key = key or os.environ.get("HUB_PRIVACY_KEY") or ""
    if not key:
        raise VaultKeyError("HUB_PRIVACY_KEY is not set")
    try:
        return Fernet(key.encode())
    except ValueError as exc:
        raise VaultKeyError("HUB_PRIVACY_KEY is not a valid Fernet key") from exc


def generate_key() -> str:
    return Fernet.generate_key().decode()


class Vault:
    def __init__(self) -> None:
        self._by_value: dict[tuple[str, str], str] = {}   # (kind, value) -> placeholder
        self._by_placeholder: dict[str, str] = {}          # placeholder -> value
        self._canonical: dict[str, str] = {}               # placeholder -> canonical form
        self._counts: dict[str, int] = {}

    def placeholder_for(self, kind: str, canonical: str, original: Optional[str] = None) -> str:
        """`canonical` decides identity (so a spaced and an unspaced IBAN share a
        placeholder); `original` is the first spelling seen, which is what
        restore() puts back."""
        key = (kind, canonical)
        if key not in self._by_value:
            n = self._counts.get(kind, 0) + 1
            self._counts[kind] = n
            ph = f"[{kind}_{n}]"
            self._by_value[key] = ph
            self._by_placeholder[ph] = original if original is not None else canonical
            self._canonical[ph] = canonical
        return self._by_value[key]

    def restore(self, text: str) -> str:
        """Put real values back. Only ever called on the VPS, on a model reply."""
        # Longest placeholders first so [NIF_10] is not clobbered by [NIF_1].
        for ph in sorted(self._by_placeholder, key=len, reverse=True):
            text = text.replace(ph, self._by_placeholder[ph])
        return text

    def __len__(self) -> int:
        return len(self._by_placeholder)

    def seal(self, key: Optional[str] = None) -> bytes:
        payload = json.dumps({"p": self._by_placeholder, "k": self._canonical, "c": self._counts}).encode()
        return _fernet(key).encrypt(payload)

    @classmethod
    def open(cls, sealed: bytes, key: Optional[str] = None) -> "Vault":
        try:
            data = json.loads(_fernet(key).decrypt(sealed))
        except InvalidToken as exc:
            raise VaultKeyError("sealed vault cannot be opened with this key") from exc
        vault = cls()
        vault._counts = {k: int(v) for k, v in data["c"].items()}
        for ph, value in data["p"].items():
            kind = ph[1:ph.rindex("_")]
            canonical = data["k"][ph]
            vault._by_placeholder[ph] = value
            vault._canonical[ph] = canonical
            vault._by_value[(kind, canonical)] = ph
        return vault
