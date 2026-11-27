"""Server-side AES-256-GCM Secret Store for Meta tokens (Section 8.2 & 13).

Raw Meta access tokens NEVER touch ordinary PostgreSQL columns, API responses, or logs.
Postgres stores only opaque `secret://meta/{workspace_id}/{uuid}` references.
"""

from __future__ import annotations

import hashlib
import os
import threading
from typing import Protocol
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from packages.shared.config import BaseAppSettings
from packages.shared.errors import NotFoundError


class SecretStore(Protocol):
    """Cloud-agnostic secret manager interface (KMS/Secrets Manager/Encrypted Vault)."""

    def put_secret(self, *, workspace_id: UUID, namespace: str, secret_value: str) -> str: ...

    def get_secret(self, *, secret_ref: str) -> str: ...

    def delete_secret(self, *, secret_ref: str) -> None: ...


class EncryptedSecretStore:
    """In-process AES-256-GCM encrypted secret store keyed from server-only env configuration."""

    def __init__(self, *, master_key_material: str) -> None:
        key_bytes = hashlib.sha256(master_key_material.encode("utf-8")).digest()
        self._aead = AESGCM(key_bytes)
        self._lock = threading.Lock()
        self._vault: dict[str, tuple[bytes, bytes]] = {}

    @classmethod
    def from_settings(cls, settings: BaseAppSettings) -> EncryptedSecretStore:
        material = (
            settings.meta_secret_encryption_key.get_secret_value().strip()
            or settings.app_jwt_secret.get_secret_value().strip()
            or "metuse-dev-only-meta-secret-vault-key"
        )
        return cls(master_key_material=material)

    def put_secret(self, *, workspace_id: UUID, namespace: str, secret_value: str) -> str:
        if not secret_value or not secret_value.strip():
            raise ValueError("Secret value must not be empty")
        ref = f"secret://{namespace}/{workspace_id}/{uuid4().hex}"
        nonce = os.urandom(12)
        aad = ref.encode("utf-8")
        ciphertext = self._aead.encrypt(nonce, secret_value.encode("utf-8"), aad)
        with self._lock:
            self._vault[ref] = (nonce, ciphertext)
        return ref

    def get_secret(self, *, secret_ref: str) -> str:
        with self._lock:
            entry = self._vault.get(secret_ref)
        if entry is None:
            raise NotFoundError(
                "Secret reference not found or revoked",
                details={"secret_ref_present": False},
            )
        nonce, ciphertext = entry
        plaintext = self._aead.decrypt(nonce, ciphertext, secret_ref.encode("utf-8"))
        return plaintext.decode("utf-8")

    def delete_secret(self, *, secret_ref: str) -> None:
        with self._lock:
            self._vault.pop(secret_ref, None)

    def has_secret(self, *, secret_ref: str) -> bool:
        with self._lock:
            return secret_ref in self._vault
