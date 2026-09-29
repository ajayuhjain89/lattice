"""Shared pieces for answering tests: a generation factory that records (and can
script) what providers receive, and configuration helpers for hand-built worlds."""

import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import SecretStr

from lattice.answering.models import AskRequest
from lattice.answering.service import AskDependencies, AskOutcome, answer_question, ask
from lattice.config import Environment
from lattice.db.engine import Database
from lattice.providers import configuration
from lattice.providers.generation import (
    GenerationProvider,
    GenerationRequest,
    GenerationResult,
    RecordingGenerationProvider,
    ScriptedGenerationProvider,
)
from lattice.providers.generation_factory import Declaration, DefaultGenerationFactory
from lattice.providers.records import CredentialRef, GenerationConfig
from lattice.providers.secrets import EnvironmentSecretStore

from ..retrieval.world import FAKE, NOW, Org

ACTOR = "test:operator"


Output = dict[str, Any] | str | BaseException | Callable[[str], dict[str, Any] | str]


@dataclass
class PromptScripted:
    """Like ``ScriptedGenerationProvider``, but an output may be a function of the
    prompt (to cite the handles this request actually assigned)."""

    outputs: Sequence[Output]
    provider: str
    model: str
    deployment: Any
    training_opt_out: bool
    retention_mode: str = "none"
    calls: int = 0

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        output = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        if callable(output) and not isinstance(output, BaseException):
            output = output(request.prompt)
        inner = ScriptedGenerationProvider([output])
        return await inner.generate(request)


def handles(prompt: str) -> dict[str, str]:
    """title -> handle, from a prompt's evidence blocks (first block per title)."""
    found: dict[str, str] = {}
    for match in re.finditer(r'<evidence-[0-9a-f]{12} id="(E\d+)"[^>]* title="([^"]*)"', prompt):
        found.setdefault(match[2], match[1])
    return found


@dataclass
class Harness:
    """The product's factory (declarations unchanged), with every built provider wrapped
    in a recorder. ``outputs`` replaces the provider's answers with scripted ones (the
    adapter's declared trust class is kept). ``secrets_seen`` records the secret handed
    to ``build`` -- tests assert it never appears anywhere else."""

    env: Environment = Environment.TEST
    outputs: Sequence[Output] | None = None
    requests: list[GenerationRequest] = field(default_factory=list)
    builds: int = 0
    secrets_seen: list[SecretStr] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.inner = DefaultGenerationFactory(self.env)

    def declare(self, config: GenerationConfig, credential: CredentialRef | None) -> Declaration:
        return self.inner.declare(config, credential)

    def build(
        self, config: GenerationConfig, credential: CredentialRef | None, secret: SecretStr | None
    ) -> GenerationProvider:
        self.builds += 1
        if secret is not None:
            self.secrets_seen.append(secret)
        provider = self.inner.build(config, credential, secret)
        if self.outputs is not None:
            provider = PromptScripted(
                self.outputs,
                provider=provider.provider,
                model=provider.model,
                deployment=provider.deployment,
                training_opt_out=provider.training_opt_out,
            )
        recorder = RecordingGenerationProvider(provider)
        recorder.requests = self.requests
        return recorder

    @property
    def payloads(self) -> list[str]:
        return [f"{r.system}\n{r.prompt}" for r in self.requests]


def deps(harness: Harness, memory: dict[str, SecretStr] | None = None) -> AskDependencies:
    return AskDependencies(
        embedding=FAKE,
        generation=harness,
        secrets=EnvironmentSecretStore(environ={}, memory=memory if memory is not None else {}),
    )


async def configure_generation(
    org: Org,
    provider: str = "fake",
    model: str = "fake-extractive-v1",
    *,
    deployment: str = "external_api",
    endpoint: str | None = None,
    level: str | None = None,
    training_opt_out: bool = True,
) -> None:
    async with org.owner.tenant_transaction(org.id) as s:
        await configuration.configure_provider(
            s,
            org.id,
            purpose="generation",
            provider=provider,
            model=model,
            deployment=deployment,
            endpoint=endpoint,
            retention_mode="none",
            training_opt_out=training_opt_out,
            reason="test setup",
            actor=ACTOR,
        )
        if level is not None:
            await configuration.approve(
                s,
                org.id,
                purpose="generation",
                provider=provider,
                model=model,
                level=level,
                reason="test setup",
                actor=ACTOR,
            )


async def set_credential(
    org: Org, provider: str, reference: str, data_use: str | None = None
) -> None:
    async with org.owner.tenant_transaction(org.id) as s:
        await configuration.set_credential(
            s,
            org.id,
            provider=provider,
            secret_ref=reference,
            data_use=data_use,  # type: ignore[arg-type]
            reason="test setup",
            actor=ACTOR,
        )


async def run(
    app: Database,
    org: Org,
    member: str,
    question: str,
    harness: Harness,
    *,
    memory: dict[str, SecretStr] | None = None,
    persist: bool = False,
    **request: Any,
) -> AskOutcome:
    body = AskRequest.model_validate({"question": question, **request})
    call = ask if persist else answer_question
    return await call(
        app,
        organization_id=org.id,
        membership_id=org.members[member],
        request=body,
        deps=deps(harness, memory),
        now=NOW,
    )


def claim(text: str, *citations: tuple[str, str], role: str = "direct_answer") -> dict[str, Any]:
    return {
        "text": text,
        "role": role,
        "citations": [{"handle": h, "quote": q} for h, q in citations],
    }


def draft(
    *claims: dict[str, Any],
    status: str = "answered",
    conflicts: Sequence[dict[str, Any]] = (),
    unknowns: Sequence[str] = (),
    clarifying_question: str = "",
) -> dict[str, Any]:
    return {
        "claims": list(claims),
        "conflicts": list(conflicts),
        "unknowns": list(unknowns),
        "clarifying_question": clarifying_question,
        "status": status,
    }


def chunk_of(org: Org, key: str, ordinal: int = 0) -> uuid.UUID:
    return org.chunks[key][ordinal]
