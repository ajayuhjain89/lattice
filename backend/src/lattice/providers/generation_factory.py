"""Build an organization's generation provider from its recorded configuration
(ADR-011, D-16, ADR-012).

Two steps, deliberately separated so that a credential can never influence policy:

1. ``declare`` -- what the implementation *would* run as (trust class, data terms,
   whether it needs a credential), from the configuration alone. The invocation rule
   (``lattice.providers.egress.provider_decision``) compares this with the record.
2. ``build`` -- only after the rule has allowed the call and the credential reference
   has been resolved: the adapter itself.

Provider names (``model_providers.provider``, purpose ``generation``):

* ``fake`` -- test double; refused in production.
* ``ollama`` -- local open-weight model server (ADR-012). The trust class follows the
  endpoint: loopback = ``local``, otherwise ``organization_controlled`` (HTTPS only).
  No credential.
* ``gemini`` -- Gemini API, organization BYOK; needs a credential. An ``unpaid`` key
  (the default declaration: Google may use the content) is refused in production.

Anything else is unsupported: nothing is sent. OpenAI, Anthropic and other endpoints fit
the same two functions when they are built; none of them is implemented in Phase 6.
"""

from dataclasses import dataclass
from typing import Protocol

from pydantic import SecretStr

from lattice.config import Environment
from lattice.providers.egress import TrustClass
from lattice.providers.gemini_generation import GeminiGenerationProvider
from lattice.providers.generation import FakeGenerationProvider, GenerationProvider
from lattice.providers.ollama_generation import (
    DEFAULT_ENDPOINT,
    OllamaGenerationProvider,
    endpoint_deployment,
    validate_endpoint,
)
from lattice.providers.records import CredentialRef, GenerationConfig

SUPPORTED: tuple[str, ...] = ("fake", "ollama", "gemini")


class GenerationUnavailable(Exception):
    """The configuration cannot be turned into a provider. ``code`` is fixed:
    ``provider_unsupported``, ``endpoint_invalid``, ``refused_in_production``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Declaration:
    deployment: TrustClass
    training_opt_out: bool
    requires_credential: bool


class GenerationFactory(Protocol):
    def declare(
        self, config: GenerationConfig, credential: CredentialRef | None
    ) -> Declaration: ...

    def build(
        self, config: GenerationConfig, credential: CredentialRef | None, secret: SecretStr | None
    ) -> GenerationProvider: ...


@dataclass(frozen=True)
class DefaultGenerationFactory:
    env: Environment
    ollama_timeout_seconds: float = 300.0
    # Test double only (refused in production by configuration): see
    # ``Settings.fake_generation_delay_seconds``.
    fake_delay_seconds: float = 0.0

    def declare(self, config: GenerationConfig, credential: CredentialRef | None) -> Declaration:
        name = config.record.provider
        if name == "fake":
            return Declaration("external_api", True, False)
        if name == "ollama":
            try:
                endpoint = validate_endpoint(config.endpoint or DEFAULT_ENDPOINT)
            except ValueError:
                raise GenerationUnavailable("endpoint_invalid") from None
            return Declaration(endpoint_deployment(endpoint), True, False)
        if name == "gemini":
            paid = credential is not None and credential.data_use == "paid"
            return Declaration("external_api", paid, True)
        raise GenerationUnavailable("provider_unsupported")

    def build(
        self, config: GenerationConfig, credential: CredentialRef | None, secret: SecretStr | None
    ) -> GenerationProvider:
        name = config.record.provider
        production = self.env is Environment.PRODUCTION
        if name == "fake":
            if production:
                raise GenerationUnavailable("refused_in_production")
            return FakeGenerationProvider(
                model=config.record.model, delay_seconds=self.fake_delay_seconds
            )
        if name == "ollama":
            return OllamaGenerationProvider(
                model=config.record.model,
                endpoint=config.endpoint or DEFAULT_ENDPOINT,
                timeout_seconds=self.ollama_timeout_seconds,
            )
        if name == "gemini":
            if secret is None:
                raise GenerationUnavailable("credential_unavailable")
            paid = credential is not None and credential.data_use == "paid"
            if production and not paid:
                raise GenerationUnavailable("refused_in_production")
            return GeminiGenerationProvider(
                secret, model=config.record.model, data_use="paid" if paid else "unpaid"
            )
        raise GenerationUnavailable("provider_unsupported")
