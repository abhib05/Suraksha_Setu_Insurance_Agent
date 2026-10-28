"""Identity vault: personal identifiers are Fernet-encrypted in the `vault` schema and the
rest of the system only ever sees `tok_...` tokens. Every reveal is audited."""

import base64
import hashlib
import hmac
from enum import StrEnum

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from suraksha import audit
from suraksha.config import get_settings
from suraksha.db.models import VaultSecret


class SecretKind(StrEnum):
    NAME = "name"
    PHONE = "phone"
    ACCOUNT = "account"
    CARD = "card"
    AADHAAR = "aadhaar"
    ADDRESS = "address"


class VaultError(RuntimeError):
    pass


def _fernet(key: str | None = None) -> Fernet:
    key = key or get_settings().vault_key
    if not key:
        raise VaultError("VAULT_KEY is not set; see .env.example")
    return Fernet(key.encode())


def _normalise(value: str) -> str:
    return " ".join(value.split()).casefold()


def token_for(kind: SecretKind, value: str, *, key: str | None = None) -> str:
    """The same person's name gets the same token wherever it appears (typed in the chat,
    read off the death certificate, the passbook), so agents can see that two documents
    agree without seeing the name. Keyed with the vault key: without it a token cannot be
    matched against a list of guessed names."""
    key = key or get_settings().vault_key
    if not key:
        raise VaultError("VAULT_KEY is not set; see .env.example")
    msg = f"{kind.value}:{_normalise(value)}".encode()
    digest = hmac.new(key.encode(), msg, hashlib.sha256).digest()
    return f"tok_{kind.value[:3]}_{base64.urlsafe_b64encode(digest[:12]).decode()}"


def tokenize(session: Session, kind: SecretKind, value: str, *, key: str | None = None) -> str:
    token = token_for(kind, value, key=key)
    if session.get(VaultSecret, token) is not None:
        return token
    ciphertext = _fernet(key).encrypt(value.strip().encode())
    try:
        with session.begin_nested():  # another request may store the same value meanwhile
            session.add(VaultSecret(token=token, kind=kind.value, ciphertext=ciphertext))
    except IntegrityError:
        pass
    return token


def reveal(
    session: Session,
    token: str,
    *,
    actor: str,
    purpose: str,
    case_id: str | None = None,
    key: str | None = None,
) -> str:
    """Decrypt one value. Callers say who they are and why; both land in the audit log."""
    row = session.get(VaultSecret, token)
    if row is None:
        raise VaultError(f"unknown token {token}")
    try:
        value = _fernet(key).decrypt(row.ciphertext).decode()
    except InvalidToken as e:
        raise VaultError("vault key does not match this secret") from e
    audit.append(
        session,
        actor=actor,
        action="vault.reveal",
        case_id=case_id,
        detail={"token": token, "kind": row.kind, "purpose": purpose},
    )
    return value


def encrypt_bytes(data: bytes, *, key: str | None = None) -> bytes:
    return _fernet(key).encrypt(data)


def decrypt_bytes(data: bytes, *, key: str | None = None) -> bytes:
    return _fernet(key).decrypt(data)
