"""The deployment-held master keys (phase-12-plan C3.3; ADR-011 amendment).

``LATTICE_SECRET_KEYS`` lists every key this process may use, ``id:<base64 of 32
bytes>`` separated by commas; ``LATTICE_SECRET_ACTIVE_KEY`` names the one new
ciphertext is sealed with. Older keys stay listed, decrypt-only, until ``lattice secrets
rekey`` has moved every ciphertext to the active key. Keys live in the deployment's
environment / secret mechanism only: never in PostgreSQL, never in an image, never in a
log line, an error or a ``repr``.

Only the processes that present organization credentials hold keys: the API (OIDC
client secrets, generation-provider keys) and the operator for ``org create`` and
``secrets rekey``. The worker, the migration job and the web app never do (C3.4): a
production worker refuses to start with them in its environment, and ``check-config``
reports them for the worker and the migration job (``misplaced_keys_problem``).
"""

import base64
import binascii
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEYS_VARIABLE: Final = "LATTICE_SECRET_KEYS"
ACTIVE_VARIABLE: Final = "LATTICE_SECRET_ACTIVE_KEY"
KEY_VARIABLES: Final = (KEYS_VARIABLE, ACTIVE_VARIABLE)
KEY_ID: Final = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
KEY_BYTES: Final = 32


class KeyringError(Exception):
    """The key configuration is unusable. The message names variables and key IDs,
    never key material."""


@dataclass(frozen=True)
class Keyring:
    """Master keys by ID. ``active`` is None when no key is configured: nothing can be
    sealed or opened (``vault:`` references fail closed as ``key_unavailable``)."""

    active: str | None = None
    _keys: Mapping[str, AESGCM] = field(default_factory=dict, repr=False)

    def __repr__(self) -> str:
        return f"Keyring(active={self.active!r}, ids={sorted(self._keys)!r})"

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(self._keys)

    def cipher(self, key_id: str) -> AESGCM | None:
        return self._keys.get(key_id)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> "Keyring":
        raw = environ.get(KEYS_VARIABLE, "").strip()
        active = environ.get(ACTIVE_VARIABLE, "").strip()
        if not raw and not active:
            return cls()
        if not raw:
            raise KeyringError(f"{ACTIVE_VARIABLE} is set but {KEYS_VARIABLE} is not")
        if not active:
            raise KeyringError(f"{KEYS_VARIABLE} is set but {ACTIVE_VARIABLE} is not")
        keys: dict[str, AESGCM] = {}
        for position, entry in enumerate(raw.split(","), start=1):
            key_id, sep, encoded = entry.strip().partition(":")
            if not sep or not KEY_ID.fullmatch(key_id):
                raise KeyringError(
                    f"{KEYS_VARIABLE} entry {position} must be <id>:<base64 key>, the ID "
                    "lower-case letters, digits, '-' or '_' (at most 32)"
                )
            if key_id in keys:
                raise KeyringError(f"{KEYS_VARIABLE} lists key {key_id!r} twice")
            try:
                material = base64.b64decode(encoded.strip(), validate=True)
            except (binascii.Error, ValueError):
                raise KeyringError(f"{KEYS_VARIABLE} key {key_id!r} is not valid base64") from None
            if len(material) != KEY_BYTES:
                raise KeyringError(
                    f"{KEYS_VARIABLE} key {key_id!r} must be exactly {KEY_BYTES} bytes "
                    "(`lattice secrets new-key`)"
                )
            keys[key_id] = AESGCM(material)
        if active not in keys:
            raise KeyringError(f"{ACTIVE_VARIABLE} names a key that {KEYS_VARIABLE} does not list")
        return cls(active=active, _keys=keys)


def configured_key_ids(environ: Mapping[str, str]) -> frozenset[str] | None:
    """The key IDs ``LATTICE_SECRET_KEYS`` lists (material is not decoded), or None
    when the variable is not set in this environment."""
    raw = environ.get(KEYS_VARIABLE, "").strip()
    if not raw:
        return None
    return frozenset(entry.strip().partition(":")[0] for entry in raw.split(","))


def misplaced_keys_problem(
    process: str, production: bool, environ: Mapping[str, str]
) -> str | None:
    """The worker and the migration job must not hold master keys in production: they
    present no organization credential (C3.4). Names the variables, never values."""
    if not production:
        return None
    present = [name for name in KEY_VARIABLES if environ.get(name)]
    if not present:
        return None
    return (
        f"{', '.join(present)} must not be set for the {process} when LATTICE_ENV=production: "
        "master keys belong to the API and operators only"
    )


def new_key_line(key_id: str, material: bytes) -> str:
    """``id:<base64>`` for ``LATTICE_SECRET_KEYS`` (``lattice secrets new-key``)."""
    if not KEY_ID.fullmatch(key_id):
        raise KeyringError("a key ID is lower-case letters, digits, '-' or '_' (at most 32)")
    if len(material) != KEY_BYTES:
        raise KeyringError(f"a key is exactly {KEY_BYTES} bytes")
    return f"{key_id}:{base64.b64encode(material).decode('ascii')}"
