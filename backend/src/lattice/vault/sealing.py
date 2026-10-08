"""Recoverable credentials sealed with AES-256-GCM (phase-12-plan C3.2).

A recoverable credential -- an OIDC client secret, a generation provider's API key --
is stored as ciphertext in ``encrypted_secrets`` and referenced as ``vault:<secret id>``
from the record that uses it. Construction:

* AEAD **AES-256-GCM** (``cryptography``'s ``AESGCM``), a fresh 96-bit nonce from
  ``os.urandom`` per encryption, the 128-bit tag appended to the ciphertext;
* **associated data** (authenticated, not secret)
  ``lattice-secret/v1|<organization>|<secret id>|<owner kind>|<owner id>|<key id>``.

When a credential is opened, the associated data is rebuilt from what the *caller*
expects -- the organization it is acting for, the secret ID its own record references,
the kind of credential it needs and that record's ID -- never from the stored row. A
ciphertext copied to another organization, another secret row, another owner record or
another credential kind, or re-labelled with another key ID, fails authentication; so
does any change to the nonce, the tag or the ciphertext. There is no fallback: a failure
is ``SecretUnavailable`` with a fixed code, a ``secret.decrypt_failed`` log event (the
secret ID and the code, never a value) and a metric.
"""

import logging
import os
import uuid
from dataclasses import dataclass
from typing import Final, Literal

from cryptography.exceptions import InvalidTag
from pydantic import SecretStr

from lattice import metrics
from lattice.log import log_event
from lattice.vault.errors import SecretUnavailable
from lattice.vault.keys import Keyring

logger = logging.getLogger(__name__)

OwnerKind = Literal["oidc_client_secret", "provider_api_key"]
OWNER_KINDS: Final = ("oidc_client_secret", "provider_api_key")
AAD_VERSION: Final = "lattice-secret/v1"
NONCE_BYTES: Final = 12
MAX_SECRET_BYTES: Final = 4096
REFERENCE_PREFIX: Final = "vault:"


def reference_for(secret_id: uuid.UUID) -> str:
    return f"{REFERENCE_PREFIX}{secret_id}"


def vault_secret_id(reference: str) -> uuid.UUID | None:
    """The secret ID of a ``vault:`` reference (canonical UUID form only), else None."""
    if not reference.startswith(REFERENCE_PREFIX):
        return None
    raw = reference.removeprefix(REFERENCE_PREFIX)
    try:
        parsed = uuid.UUID(raw)
    except ValueError:
        return None
    return parsed if str(parsed) == raw else None


def associated_data(
    *,
    organization_id: uuid.UUID,
    secret_id: uuid.UUID,
    owner_kind: str,
    owner_id: uuid.UUID,
    key_id: str,
) -> bytes:
    if owner_kind not in OWNER_KINDS:
        raise ValueError("unknown owner kind")
    fields = (AAD_VERSION, str(organization_id), str(secret_id), owner_kind, str(owner_id), key_id)
    return "|".join(fields).encode("ascii")


@dataclass(frozen=True)
class Binding:
    """What a ciphertext must be bound to: supplied by the caller from its own record."""

    organization_id: uuid.UUID
    secret_id: uuid.UUID
    owner_kind: OwnerKind
    owner_id: uuid.UUID


@dataclass(frozen=True)
class Sealed:
    """Stored material (all None once destroyed)."""

    key_id: str | None
    nonce: bytes | None
    ciphertext: bytes | None

    def __repr__(self) -> str:
        return f"Sealed(key_id={self.key_id!r})"


@dataclass(frozen=True)
class SealedSecret:
    """A ``vault:`` credential as loaded for use: the expected binding and the stored
    material. ``material`` is None when the row does not exist."""

    binding: Binding
    material: Sealed | None


def normalise_value(value: SecretStr) -> SecretStr:
    """Surrounding whitespace removed (a value read from stdin ends with a newline);
    refused when empty or longer than ``MAX_SECRET_BYTES``. The message never holds it."""
    stripped = value.get_secret_value().strip()
    if not stripped:
        raise ValueError("the secret is empty")
    if len(stripped.encode("utf-8")) > MAX_SECRET_BYTES:
        raise ValueError(f"the secret is longer than {MAX_SECRET_BYTES} bytes")
    return SecretStr(stripped)


def seal(keyring: Keyring, value: SecretStr, binding: Binding) -> Sealed:
    """Encrypt under the active key. ``SecretUnavailable("no_active_key")`` when the
    process holds no key."""
    if keyring.active is None:
        raise SecretUnavailable("no_active_key")
    cipher = keyring.cipher(keyring.active)
    if cipher is None:  # pragma: no cover - Keyring guarantees the active key exists
        raise SecretUnavailable("no_active_key")
    nonce = os.urandom(NONCE_BYTES)
    aad = associated_data(
        organization_id=binding.organization_id,
        secret_id=binding.secret_id,
        owner_kind=binding.owner_kind,
        owner_id=binding.owner_id,
        key_id=keyring.active,
    )
    ciphertext = cipher.encrypt(nonce, normalise_value(value).get_secret_value().encode(), aad)
    return Sealed(key_id=keyring.active, nonce=nonce, ciphertext=ciphertext)


def _fail(secret_id: uuid.UUID, code: str) -> SecretUnavailable:
    metrics.secret_decrypt_failed(code)
    log_event(logger, logging.WARNING, "secret.decrypt_failed", secret_id=str(secret_id), code=code)
    return SecretUnavailable(code)


def open_sealed(keyring: Keyring, secret: SealedSecret) -> SecretStr:
    """Decrypt, binding to ``secret.binding``. Codes: ``missing`` (no row),
    ``destroyed``, ``key_unavailable`` (the key ID is not configured here),
    ``decrypt_failed`` (authentication failed: tampered, moved or a wrong key)."""
    binding = secret.binding
    material = secret.material
    if material is None:
        raise _fail(binding.secret_id, "missing")
    if material.ciphertext is None or material.nonce is None or material.key_id is None:
        raise _fail(binding.secret_id, "destroyed")
    cipher = keyring.cipher(material.key_id)
    if cipher is None:
        raise _fail(binding.secret_id, "key_unavailable")
    aad = associated_data(
        organization_id=binding.organization_id,
        secret_id=binding.secret_id,
        owner_kind=binding.owner_kind,
        owner_id=binding.owner_id,
        key_id=material.key_id,
    )
    try:
        plaintext = cipher.decrypt(material.nonce, material.ciphertext, aad)
    except (InvalidTag, ValueError):
        raise _fail(binding.secret_id, "decrypt_failed") from None
    try:
        return SecretStr(plaintext.decode("utf-8"))
    except UnicodeDecodeError:  # pragma: no cover - authenticated, so written by seal()
        raise _fail(binding.secret_id, "decrypt_failed") from None


def hint_for(owner_kind: OwnerKind, value: SecretStr) -> str | None:
    """The last four characters of a long provider API key (shown to recognise it);
    never for an OIDC client secret, never for a short value."""
    text = value.get_secret_value().strip()
    if owner_kind != "provider_api_key" or len(text) < 20:
        return None
    hint = text[-4:]
    return hint if hint.isascii() and hint.isprintable() else None
