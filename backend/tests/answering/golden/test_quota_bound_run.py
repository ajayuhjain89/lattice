"""A quota-bound answer evaluation over the real synthetic corpus (phase-13-followups §12,
E1-E2): a run that meets a per-day quota stops after that item and sends nothing more,
and resuming its report asks only what is missing and ends where an uninterrupted run
ends. The fake provider stands in for Gemini; only the quota answer is simulated."""

import json
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import SecretStr

from lattice.answering.prompt import PromptConfig
from lattice.config import Environment
from lattice.dataset.lint import Dataset
from lattice.db.engine import Database
from lattice.evals.answer import (
    QUOTA_EXHAUSTED,
    AnswerReport,
    GenerationSpec,
    Pacer,
    evaluate_answers,
)
from lattice.evals.retrieval import Corpus, Identities, select_items
from lattice.providers.embedding import FakeEmbeddingProvider
from lattice.providers.generation import (
    GenerationError,
    GenerationProvider,
    GenerationRequest,
    GenerationResult,
    RecordingGenerationProvider,
)
from lattice.providers.generation_factory import Declaration, DefaultGenerationFactory
from lattice.providers.records import CredentialRef, GenerationConfig
from lattice.providers.secrets import EnvironmentSecretStore

FAKE_SPEC = GenerationSpec("fake", "fake-extractive-v1", "external_api")


@dataclass
class _Budgeted(RecordingGenerationProvider):
    owner: "QuotaAfter | None" = None

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        assert self.owner is not None
        self.owner.sent += 1
        if self.owner.sent > self.owner.allowed:
            raise GenerationError(QUOTA_EXHAUSTED)
        return await self.inner.generate(request)


@dataclass
class QuotaAfter:
    """The product's factory, whose providers share a per-day budget of ``allowed``
    calls (the evaluation builds one provider per Ask)."""

    allowed: int
    sent: int = 0

    def __post_init__(self) -> None:
        self.inner = DefaultGenerationFactory(Environment.TEST)

    def declare(self, config: GenerationConfig, credential: CredentialRef | None) -> Declaration:
        return self.inner.declare(config, credential)

    def build(
        self, config: GenerationConfig, credential: CredentialRef | None, secret: SecretStr | None
    ) -> GenerationProvider:
        return _Budgeted(self.inner.build(config, credential, secret), owner=self)


def _outcomes(report: AnswerReport) -> list[tuple[Any, ...]]:
    return [
        (r.id, r.egress_config, r.status, r.status_reason, r.claims, r.cited_records)
        for r in report.items
    ]


async def test_a_stopped_run_resumes_to_the_uninterrupted_result(
    app_db: Database,
    owner_db: Database,
    dataset: Dataset,
    identities: Identities,
    corpus: Corpus,
) -> None:
    items = [
        i
        for i in select_items(dataset)
        if i.split == "dev" and i.expected is not None and i.expected.status == "answered"
    ][:10]
    common: dict[str, Any] = {
        "identities": identities,
        "corpus": corpus,
        "spec": FAKE_SPEC,
        "secrets": EnvironmentSecretStore(environ={}),
        "embedding": FakeEmbeddingProvider(),
        "items": items,
    }
    product = DefaultGenerationFactory(Environment.TEST)
    full = await evaluate_answers(app_db, owner_db, dataset, factory=product, **common)
    assert sum(r.model_called for r in full.items) >= 5

    quota = QuotaAfter(allowed=3)
    partial = await evaluate_answers(
        app_db, owner_db, dataset, factory=quota, pacer=Pacer(), **common
    )
    assert quota.sent == 4  # three answered, the fourth met the quota, then nothing
    assert partial.pacing is not None
    assert partial.pacing["stopped"] == "quota_exhausted"
    assert partial.pacing["not_run"] == len(full.items) - len(partial.items) > 0
    failed = [r for r in partial.items if r.status == "failed"]
    assert [r.status_reason for r in failed] == ["provider_unavailable"]

    previous = json.loads(json.dumps(partial.to_json(), default=str))
    resumed = await evaluate_answers(
        app_db,
        owner_db,
        dataset,
        factory=product,
        previous=previous,
        previous_name="answer-partial",
        **common,
    )
    assert resumed.resumed_from == ["answer-partial"]
    assert _outcomes(resumed) == _outcomes(full)
    assert resumed.summary["ran"] == full.summary["ran"]
    assert resumed.summary["status_accuracy"] == full.summary["status_accuracy"]
    assert all(v == 0 for v in resumed.summary["security"].values())

    with pytest.raises(ValueError, match="prompt_config differs"):
        await evaluate_answers(
            app_db,
            owner_db,
            dataset,
            factory=product,
            previous=previous,
            prompt_config=PromptConfig(max_evidence=16),
            **common,
        )
