"""Server-side AES-256-GCM Secret Store for Meta tokens (Section 8.2 & 13).

Raw Meta access tokens NEVER touch ordinary PostgreSQL columns, API responses, or logs.
Postgres stores only opaque `secret://meta/{workspace_id}/{uuid}` references plus the
AES-256-GCM envelope (nonce + ciphertext) in the workspace-isolated `secret_vault` table.

Backends:
- `EncryptedSecretStore` (in-memory): dev/test only, tokens vanish on restart.
- `DbSecretStore` (Postgres `secret_vault`): required outside dev/test.
"""

from __future__ import annotations

import hashlib
import os
import threading
from typing import Protocol
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from database.models import SecretVault
from packages.shared.config import BaseAppSettings
from packages.shared.errors import ExternalServiceError, NotFoundError
from packages.tenancy.db import rls_transaction

_DEV_ONLY_VAULT_KEY = "metuse-dev-only-meta-secret-vault-key"


class SecretStore(Protocol):
    """Cloud-agnostic secret manager interface (KMS/Secrets Manager/Encrypted Vault)."""

    def put_secret(self, *, workspace_id: UUID, namespace: str, secret_value: str) -> str: ...

    def get_secret(self, *, secret_ref: str) -> str: ...

    def delete_secret(self, *, secret_ref: str) -> None: ...


def resolve_vault_key_material(settings: BaseAppSettings) -> str:
    """Resolve vault master key material; fail closed outside dev/test environments."""
    material = (
        settings.meta_secret_encryption_key.get_secret_value().strip()
        or settings.app_jwt_secret.get_secret_value().strip()
    )
    if material:
        return material
    if settings.is_dev_env():
        return _DEV_ONLY_VAULT_KEY
    raise RuntimeError(
        "META_SECRET_ENCRYPTION_KEY (or APP_JWT_SECRET) must be configured "
        "for the secret vault in non-development environments"
    )


def _derive_aead(master_key_material: str) -> AESGCM:
    key_bytes = hashlib.sha256(master_key_material.encode("utf-8")).digest()
    return AESGCM(key_bytes)


def parse_vault_secret_ref(secret_ref: str) -> tuple[str, UUID, str] | None:
    """Parse `secret://{namespace}/{workspace_id}/{token}` refs; None when malformed."""
    prefix = "secret://"
    cleaned = secret_ref.strip()
    if not cleaned.startswith(prefix):
        return None
    parts = cleaned[len(prefix) :].split("/")
    if len(parts) != 3 or not all(parts):
        return None
    try:
        workspace_id = UUID(parts[1])
    except ValueError:
        return None
    return parts[0], workspace_id, parts[2]


def _build_secret_ref(*, workspace_id: UUID, namespace: str) -> str:
    return f"secret://{namespace}/{workspace_id}/{uuid4().hex}"


class EncryptedSecretStore:
    """In-process AES-256-GCM encrypted secret store (dev/test only)."""

    def __init__(self, *, master_key_material: str) -> None:
        self._aead = _derive_aead(master_key_material)
        self._lock = threading.Lock()
        self._vault: dict[str, tuple[bytes, bytes]] = {}

    @classmethod
    def from_settings(cls, settings: BaseAppSettings) -> EncryptedSecretStore:
        return cls(master_key_material=resolve_vault_key_material(settings))

    def put_secret(self, *, workspace_id: UUID, namespace: str, secret_value: str) -> str:
        if not secret_value or not secret_value.strip():
            raise ValueError("Secret value must not be empty")
        ref = _build_secret_ref(workspace_id=workspace_id, namespace=namespace)
        nonce = os.urandom(12)
        ciphertext = self._aead.encrypt(nonce, secret_value.encode("utf-8"), ref.encode("utf-8"))
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
        try:
            plaintext = self._aead.decrypt(nonce, ciphertext, secret_ref.encode("utf-8"))
        except InvalidTag as exc:
            raise ExternalServiceError("Secret vault decryption failed") from exc
        return plaintext.decode("utf-8")

    def delete_secret(self, *, secret_ref: str) -> None:
        with self._lock:
            self._vault.pop(secret_ref, None)

    def has_secret(self, *, secret_ref: str) -> bool:
        with self._lock:
            return secret_ref in self._vault


class DbSecretStore:
    """Postgres-backed AES-256-GCM vault; envelopes persist across restarts/deploys.

    Each operation runs in its own short workspace-scoped RLS transaction. Callers
    are pre-authorized through tenant transactions; the unguessable secret ref acts
    as the capability and RLS confines every read/write to one workspace.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        master_key_material: str,
    ) -> None:
        self._sessions = session_factory
        self._aead = _derive_aead(master_key_material)

    @classmethod
    def from_settings(
        cls, session_factory: sessionmaker[Session], settings: BaseAppSettings
    ) -> DbSecretStore:
        return cls(session_factory, master_key_material=resolve_vault_key_material(settings))

    def put_secret(self, *, workspace_id: UUID, namespace: str, secret_value: str) -> str:
        if not secret_value or not secret_value.strip():
            raise ValueError("Secret value must not be empty")
        ref = _build_secret_ref(workspace_id=workspace_id, namespace=namespace)
        nonce = os.urandom(12)
        ciphertext = self._aead.encrypt(nonce, secret_value.encode("utf-8"), ref.encode("utf-8"))
        with rls_transaction(self._sessions, user_id=None, workspace_id=workspace_id) as session:
            session.add(
                SecretVault(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    secret_ref=ref,
                    namespace=namespace,
                    nonce=nonce,
                    ciphertext=ciphertext,
                )
            )
            session.flush()
        return ref

    def _load_envelope(self, *, secret_ref: str) -> tuple[bytes, bytes]:
        parsed = parse_vault_secret_ref(secret_ref)
        if parsed is None:
            raise NotFoundError(
                "Secret reference not found or revoked",
                details={"secret_ref_present": False},
            )
        _, workspace_id, _ = parsed
        with rls_transaction(self._sessions, user_id=None, workspace_id=workspace_id) as session:
            row = session.execute(
                select(SecretVault).where(
                    SecretVault.workspace_id == workspace_id,
                    SecretVault.secret_ref == secret_ref,
                )
            ).scalar_one_or_none()
            if row is None:
                raise NotFoundError(
                    "Secret reference not found or revoked",
                    details={"secret_ref_present": False},
                )
            return bytes(row.nonce), bytes(row.ciphertext)

    def get_secret(self, *, secret_ref: str) -> str:
        nonce, ciphertext = self._load_envelope(secret_ref=secret_ref)
        try:
            plaintext = self._aead.decrypt(nonce, ciphertext, secret_ref.encode("utf-8"))
        except InvalidTag as exc:
            raise ExternalServiceError("Secret vault decryption failed") from exc
        return plaintext.decode("utf-8")

    def delete_secret(self, *, secret_ref: str) -> None:
        parsed = parse_vault_secret_ref(secret_ref)
        if parsed is None:
            return
        _, workspace_id, _ = parsed
        with rls_transaction(self._sessions, user_id=None, workspace_id=workspace_id) as session:
            session.execute(
                delete(SecretVault).where(
                    SecretVault.workspace_id == workspace_id,
                    SecretVault.secret_ref == secret_ref,
                )
            )
            session.flush()

    def has_secret(self, *, secret_ref: str) -> bool:
        try:
            self._load_envelope(secret_ref=secret_ref)
        except NotFoundError:
            return False
        return True
