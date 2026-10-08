"""Secret management for organization-owned credentials (D-16, D-29, ADR-011).

The database never holds a credential **in plaintext**. A credential row
(``provider_credentials``, ``oidc_connections``) holds a **secret reference**: a string
naming where the value lives, resolved by a ``SecretStore`` only at invocation time,
only after the invocation rule (``lattice.providers.egress``) has already allowed the
call. A credential is necessary, never sufficient: nothing here is an egress input.

Reference schemes:

* ``env:NAME`` -- a process environment variable (the development and
  single-deployment fallback). Only names made for provider credentials resolve:
  ``LATTICE_PROVIDER_SECRET_<SUFFIX>`` plus the documented legacy names
  (``LATTICE_GEMINI_API_KEY``, ``GEMINI_API_KEY``, ``LATTICE_OPENAI_API_KEY``). A
  reference to any other variable -- a database URL, a session secret -- is refused,
  so a stored reference can never make a provider adapter send another secret of this
  process to a third party.
* ``vault:<secret id>`` -- a recoverable credential encrypted at rest in
  ``encrypted_secrets`` (AES-256-GCM, ``lattice.vault``; phase-12-plan C3). The caller
  loads the sealed row with its own binding (organization, its record's ID, the kind of
  credential it needs) and passes it as ``sealed``; the store opens it with the
  deployment's master keys (``Keyring``). A store without keys cannot open anything.
* ``memory:NAME`` -- an in-process store (tests only; refused in production).

Values are ``SecretStr`` end to end: never in ``repr``, logs, traces, prompts, API
responses or exception text. Every failure is a ``SecretUnavailable`` with a fixed code
and never the reference's value.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Protocol

from pydantic import SecretStr

from lattice.vault.errors import SecretUnavailable as SecretUnavailable
from lattice.vault.keys import Keyring
from lattice.vault.sealing import SealedSecret, open_sealed, vault_secret_id

_ENV_NAME: Final = re.compile(r"LATTICE_PROVIDER_SECRET_[A-Z0-9_]{1,64}")
LEGACY_ENV_NAMES: Final = frozenset(
    {"LATTICE_GEMINI_API_KEY", "GEMINI_API_KEY", "LATTICE_OPENAI_API_KEY"}
)
_MEMORY_NAME: Final = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
SCHEMES: Final = ("env", "memory", "vault")


def parse_reference(reference: str) -> tuple[str, str]:
    """``(scheme, name)`` of a well-formed reference; ``SecretUnavailable`` otherwise.
    Used when a reference is stored as well as when it is resolved."""
    scheme, sep, name = reference.partition(":")
    if not sep or scheme not in SCHEMES:
        raise SecretUnavailable("invalid_reference")
    if scheme == "env" and not (_ENV_NAME.fullmatch(name) or name in LEGACY_ENV_NAMES):
        raise SecretUnavailable("invalid_reference")
    if scheme == "memory" and not _MEMORY_NAME.fullmatch(name):
        raise SecretUnavailable("invalid_reference")
    if scheme == "vault" and vault_secret_id(reference) is None:
        raise SecretUnavailable("invalid_reference")
    return scheme, name


class SecretStore(Protocol):
    def resolve(self, reference: str, sealed: SealedSecret | None = None) -> SecretStr:
        """The secret, or ``SecretUnavailable``. ``sealed``: the loaded row of a
        ``vault:`` reference, bound by the caller (``lattice.vault.records``)."""
        ...

    def available(self, reference: str, sealed: SealedSecret | None = None) -> bool:
        """Whether ``resolve`` would succeed -- the masked "connected" state, without
        exposing anything about the value."""
        ...


@dataclass
class EnvironmentSecretStore:
    """``env:`` references over a mapping (the process environment by default).
    ``memory:`` references resolve only when ``memory`` is given (tests); ``vault:``
    references only with a ``keyring`` (the API and operator tools, C3.4)."""

    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)
    memory: dict[str, SecretStr] | None = None
    keyring: Keyring | None = field(default=None, repr=False)

    def resolve(self, reference: str, sealed: SealedSecret | None = None) -> SecretStr:
        scheme, name = parse_reference(reference)
        if scheme == "vault":
            if self.keyring is None:
                raise SecretUnavailable("scheme_not_allowed")
            if sealed is None or reference != f"vault:{sealed.binding.secret_id}":
                raise SecretUnavailable("missing")
            return open_sealed(self.keyring, sealed)
        if scheme == "memory":
            if self.memory is None:
                raise SecretUnavailable("scheme_not_allowed")
            value = self.memory.get(name)
            if value is None:
                raise SecretUnavailable("missing")
            if not value.get_secret_value():
                raise SecretUnavailable("empty")
            return value
        raw = self.environ.get(name)
        if raw is None:
            raise SecretUnavailable("missing")
        if not raw.strip():
            raise SecretUnavailable("empty")
        return SecretStr(raw.strip())

    def available(self, reference: str, sealed: SealedSecret | None = None) -> bool:
        try:
            self.resolve(reference, sealed)
        except SecretUnavailable:
            return False
        return True
