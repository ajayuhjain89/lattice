"""Secret management for organization-owned provider credentials (D-16, ADR-011).

The database never holds a credential. A provider credential row
(``provider_credentials``) holds a **secret reference**: a string naming where the
value lives, resolved by a ``SecretStore`` only at invocation time, only after the
invocation rule (``lattice.providers.egress``) has already allowed the call. A
credential is necessary, never sufficient: nothing here is an egress input.

Reference schemes:

* ``env:NAME`` -- a process environment variable (the development and
  single-deployment fallback). Only names made for provider credentials resolve:
  ``LATTICE_PROVIDER_SECRET_<SUFFIX>`` plus the documented legacy names
  (``LATTICE_GEMINI_API_KEY``, ``GEMINI_API_KEY``, ``LATTICE_OPENAI_API_KEY``). A
  reference to any other variable -- a database URL, a session secret -- is refused,
  so a stored reference can never make a provider adapter send another secret of this
  process to a third party.
* ``memory:NAME`` -- an in-process store (tests only; refused in production).

A production secret manager (Vault, a cloud secret manager) is another scheme behind
the same ``SecretStore`` Protocol; nothing that calls it changes.

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

_ENV_NAME: Final = re.compile(r"LATTICE_PROVIDER_SECRET_[A-Z0-9_]{1,64}")
LEGACY_ENV_NAMES: Final = frozenset(
    {"LATTICE_GEMINI_API_KEY", "GEMINI_API_KEY", "LATTICE_OPENAI_API_KEY"}
)
_MEMORY_NAME: Final = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
SCHEMES: Final = ("env", "memory")


class SecretUnavailable(Exception):
    """A reference could not be resolved. ``code`` is fixed: ``invalid_reference``,
    ``scheme_not_allowed``, ``missing``, ``empty``. Never the value or the name."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


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
    return scheme, name


class SecretStore(Protocol):
    def resolve(self, reference: str) -> SecretStr:
        """The secret, or ``SecretUnavailable``."""
        ...

    def available(self, reference: str) -> bool:
        """Whether ``resolve`` would succeed -- the masked "connected" state, without
        exposing anything about the value."""
        ...


@dataclass
class EnvironmentSecretStore:
    """``env:`` references over a mapping (the process environment by default).
    ``memory:`` references resolve only when ``memory`` is given (tests)."""

    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)
    memory: dict[str, SecretStr] | None = None

    def resolve(self, reference: str) -> SecretStr:
        scheme, name = parse_reference(reference)
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

    def available(self, reference: str) -> bool:
        try:
            self.resolve(reference)
        except SecretUnavailable:
            return False
        return True
