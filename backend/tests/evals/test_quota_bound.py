"""Quota-bound answer evaluation (phase-13-followups §12, E1, E2, E4) as pure units:
pacing, the per-minute retry, the per-day latch, what a resume keeps and refuses, and
the preflight that sends no prompt."""

import ipaddress
from typing import Any

import httpx
import pytest

from lattice.cli_answer import _previous, preflight
from lattice.evals.answer import (
    QUOTA_EXHAUSTED,
    RATE_LIMITED,
    AnswerItemResult,
    GenerationSpec,
    Pacer,
    kept_runs,
    resume_problems,
)
from lattice.providers.generation import GenerationError
from lattice.providers.tester import ProviderTester


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 3))
        self.now += seconds


def _pacer(rpm: float | None = None) -> tuple[Pacer, FakeTime]:
    time = FakeTime()
    return Pacer(rpm, retry_wait=60.0, clock=time.clock, sleep=time.sleep), time


class Calls:
    def __init__(self, *outcomes: str | None) -> None:
        self.outcomes = list(outcomes)
        self.sent = 0

    async def __call__(self) -> str:
        outcome = self.outcomes[min(self.sent, len(self.outcomes) - 1)]
        self.sent += 1
        if outcome is not None:
            raise GenerationError(outcome)
        return "ok"


async def test_calls_are_spaced_to_the_rate() -> None:
    pacer, time = _pacer(rpm=12)  # one call every 5 s
    calls = Calls(None)
    for _ in range(3):
        assert await pacer.send(calls) == "ok"
    assert time.slept == [5.0, 5.0]
    time.now += 30  # time already passed counts
    await pacer.send(calls)
    assert time.slept == [5.0, 5.0]
    assert pacer.record() == {
        "rpm": 12,
        "retries": 0,
        "waited_seconds": 10.0,
        "stopped": None,
    }


async def test_a_per_minute_limit_is_retried_once_after_a_wait() -> None:
    pacer, time = _pacer()
    assert await pacer.send(Calls(RATE_LIMITED, None)) == "ok"
    assert (time.slept, pacer.retries) == ([60.0], 1)
    twice = Calls(RATE_LIMITED, RATE_LIMITED, None)
    with pytest.raises(GenerationError, match="rate_limited"):
        await pacer.send(twice)
    assert twice.sent == 2  # one retry only: the item then fails, and a resume asks it again


async def test_a_per_day_quota_stops_every_later_call() -> None:
    pacer, _ = _pacer()
    calls = Calls(QUOTA_EXHAUSTED)
    with pytest.raises(GenerationError, match="quota_exhausted"):
        await pacer.send(calls)
    assert pacer.exhausted
    with pytest.raises(GenerationError, match="quota_exhausted"):
        await pacer.send(calls)
    assert calls.sent == 1  # the latch sends nothing more, whichever provider instance asks
    assert pacer.record()["stopped"] == "quota_exhausted"


async def test_other_failures_pass_through_untouched() -> None:
    pacer, time = _pacer()
    with pytest.raises(GenerationError, match="unauthorized"):
        await pacer.send(Calls("generation_failed:unauthorized"))
    assert (time.slept, pacer.exhausted) == ([], False)


def test_the_rate_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        Pacer(0)


def _run(item_id: str, **fields: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": item_id,
        "category": "lookup",
        "persona": "maya",
        "organization": "halden",
        "egress_config": "default",
        "question": "q?",
        "same_question_group": None,
        "tags": [],
        "expected_status": "answered",
        "expected_reason": None,
        "status": "answered",
    }
    return row | fields


IDENTITY: dict[str, Any] = {
    "kind": "answer",
    "generation": {
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "deployment": "external_api",
        "data_use": "unpaid",
    },
    "embedding_provider": None,
    "embedding_model": None,
    "judge": None,
    "gate_version": "gate_v1",
    "retrieval_config_version": "retrieval-6",
    "prompt_config": {"version": "answer-prompt-2", "max_evidence": 12},
    "corpus_fingerprint": {"sha256": "c" * 64},
    "golden_version": {"sha256": "g" * 64, "items": 3},
}


def _report(*runs: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    generation = IDENTITY["generation"] | {"credential_ref": "set", "training_opt_out": False}
    return {**IDENTITY, "generation": generation, "items": list(runs)} | overrides


def test_a_resume_keeps_finished_runs_and_asks_failed_ones_again() -> None:
    previous = _report(
        _run("done"),
        _run("abstained", status="insufficient_evidence", status_reason="no_relevant_evidence"),
        _run("quota", status="failed", status_reason="provider_unavailable"),
        _run("lost", status=None, error="persona_not_seeded"),
        _run("future", unknown_field_from_a_later_version=1),
    )
    kept = kept_runs(previous)
    assert [r.id for r in kept] == ["done", "abstained", "future"]
    assert all(isinstance(r, AnswerItemResult) for r in kept)
    assert resume_problems(previous, IDENTITY) == []


@pytest.mark.parametrize(
    ("override", "problem"),
    [
        ({"prompt_config": {"version": "answer-prompt-2", "max_evidence": 16}}, "prompt_config"),
        ({"golden_version": {"sha256": "h" * 64, "items": 3}}, "golden_version"),
        ({"corpus_fingerprint": {"sha256": "d" * 64}}, "corpus_fingerprint"),
        ({"retrieval_config_version": "retrieval-6+x=1"}, "retrieval_config_version"),
        ({"gate_version": "gate_v2"}, "gate_version"),
        ({"judge": "ollama/qwen judge-1"}, "judge"),
    ],
)
def test_a_resume_of_another_configuration_is_refused(
    override: dict[str, Any], problem: str
) -> None:
    assert resume_problems(_report(_run("done"), **override), IDENTITY) == [f"{problem} differs"]


def test_a_resume_needs_an_answer_report(tmp_path: Any) -> None:
    with pytest.raises(ValueError, match="no report"):
        _previous(tmp_path)
    (tmp_path / "report.json").write_text('{"kind": "retrieval"}')
    with pytest.raises(ValueError, match="not an answer report"):
        _previous(tmp_path)
    (tmp_path / "report.json").write_text('{"kind": "answer", "items": []}')
    data, name = _previous(tmp_path)
    assert (data, name) == ({"kind": "answer", "items": []}, tmp_path.name)


SPEC = GenerationSpec("gemini", "gemini-3.5-flash-lite", "external_api", data_use="unpaid")


@pytest.mark.parametrize(
    ("status", "verdict"),
    [(200, "valid"), (403, "invalid_credential"), (404, "configuration_invalid")],
)
async def test_the_preflight_reads_the_model_and_generates_nothing(
    monkeypatch: pytest.MonkeyPatch, status: int, verdict: str
) -> None:
    monkeypatch.setenv("LATTICE_GEMINI_API_KEY", "test-key-not-real")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={})

    tester = ProviderTester(
        transport=httpx.MockTransport(handler),
        resolver=lambda host, port: _public(),
    )
    assert await preflight(SPEC, tester) == verdict
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/v1beta/models/gemini-3.5-flash-lite")
    ]
    assert seen[0].content == b""  # no question, no evidence, no prompt


async def _public() -> list[Any]:
    return [ipaddress.ip_address("142.250.0.1")]


async def test_the_preflight_without_a_key_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LATTICE_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert await preflight(SPEC, ProviderTester(transport=httpx.MockTransport(_unreachable))) == (
        "credential_missing"
    )


def _unreachable(request: httpx.Request) -> httpx.Response:
    raise AssertionError("nothing may be sent without a key")
