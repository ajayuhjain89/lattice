"""Build the embedding provider an operator command asked for (ADR-008, ADR-009,
ADR-010).

Provider matrix (docs/architecture.md §3):

* ``fake`` -- test double for tests and CI; refused in production.
* ``local`` -- open-weight model in-process; no credential, no external egress.
* ``gemini`` -- external API, optional; an unpaid-tier key is refused in production and
  limited to synthetic organizations by the egress policy.
* ``openai`` -- external API, optional.

Building a provider never approves it: the egress policy decides, per organization,
what it may receive. A missing credential or model is a ``ConfigurationError`` naming
what to set; there is never a fallback to a different provider or model.
"""

from typing import Literal

from lattice.config import ConfigurationError, Environment, ProviderSettings
from lattice.ingestion.models import EMBEDDING_DIMENSIONS
from lattice.providers.embedding import EmbeddingProvider, FakeEmbeddingProvider
from lattice.providers.gemini_embedding import SUPPORTED_MODELS, GeminiEmbeddingProvider
from lattice.providers.local_embedding import LocalEmbeddingProvider
from lattice.providers.local_models import LocalModelUnavailable, spec_for
from lattice.providers.openai_embedding import OpenAIEmbeddingProvider

ProviderName = Literal["none", "fake", "local", "gemini", "openai"]
PROVIDER_NAMES: tuple[ProviderName, ...] = ("none", "fake", "local", "gemini", "openai")


def build_embedding_provider(
    name: ProviderName, settings: ProviderSettings
) -> EmbeddingProvider | None:
    """``none``: chunks that may be embedded wait as ``pending``."""
    if name == "none":
        return None
    if name == "fake":
        if settings.env is Environment.PRODUCTION:
            raise ConfigurationError(
                "the fake embedding provider is not available when LATTICE_ENV=production"
            )
        return FakeEmbeddingProvider(EMBEDDING_DIMENSIONS)
    if name == "local":
        try:
            spec = spec_for(settings.local_embedding_model)
            if spec.dimensions != EMBEDDING_DIMENSIONS:
                raise ConfigurationError(
                    f"{spec.name} produces {spec.dimensions} dimensions; "
                    f"this deployment stores {EMBEDDING_DIMENSIONS}"
                )
            return LocalEmbeddingProvider.from_cache(
                spec, settings.model_dir, threads=settings.local_embedding_threads
            )
        except LocalModelUnavailable as exc:
            raise ConfigurationError(str(exc)) from None
    if name == "gemini":
        key = settings.gemini_api_key
        if key is None or not key.get_secret_value():
            raise ConfigurationError(
                "LATTICE_GEMINI_API_KEY (or GEMINI_API_KEY): required for --embedding gemini"
            )
        if settings.gemini_embedding_model not in SUPPORTED_MODELS:
            raise ConfigurationError(
                f"LATTICE_GEMINI_EMBEDDING_MODEL: must be one of {', '.join(SUPPORTED_MODELS)}"
            )
        if settings.gemini_data_use == "unpaid" and settings.env is Environment.PRODUCTION:
            raise ConfigurationError(
                "an unpaid-tier Gemini key (LATTICE_GEMINI_DATA_USE=unpaid) is for synthetic "
                "development data only and is refused when LATTICE_ENV=production"
            )
        return GeminiEmbeddingProvider(
            key,
            model=settings.gemini_embedding_model,
            dimensions=EMBEDDING_DIMENSIONS,
            data_use=settings.gemini_data_use,
        )
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value():
        raise ConfigurationError("LATTICE_OPENAI_API_KEY: required for --embedding openai")
    return OpenAIEmbeddingProvider(
        settings.openai_api_key,
        model=settings.openai_embedding_model,
        dimensions=EMBEDDING_DIMENSIONS,
    )
