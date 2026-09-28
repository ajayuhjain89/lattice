"""Build the embedding provider an operator command asked for (ADR-008)."""

from typing import Literal

from lattice.config import ConfigurationError, Environment, ProviderSettings
from lattice.ingestion.models import EMBEDDING_DIMENSIONS
from lattice.providers.embedding import EmbeddingProvider, FakeEmbeddingProvider
from lattice.providers.openai_embedding import OpenAIEmbeddingProvider

ProviderName = Literal["none", "fake", "openai"]


def build_embedding_provider(
    name: ProviderName, settings: ProviderSettings
) -> EmbeddingProvider | None:
    """``none``: chunks that may be embedded wait as ``pending``. ``fake``:
    deterministic local vectors (development and tests only). ``openai``: the real
    provider; needs ``LATTICE_OPENAI_API_KEY``."""
    if name == "none":
        return None
    if name == "fake":
        if settings.env is Environment.PRODUCTION:
            raise ConfigurationError(
                "the fake embedding provider is not available when LATTICE_ENV=production"
            )
        return FakeEmbeddingProvider(EMBEDDING_DIMENSIONS)
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value():
        raise ConfigurationError("LATTICE_OPENAI_API_KEY: required for --embedding openai")
    return OpenAIEmbeddingProvider(
        settings.openai_api_key,
        model=settings.openai_embedding_model,
        dimensions=EMBEDDING_DIMENSIONS,
    )
