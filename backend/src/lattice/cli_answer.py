"""``lattice ask`` and ``lattice eval answer`` (Phase 6).

Both act *as a member* (like ``search`` and ``eval retrieval``): they resolve a
persona's membership with the operator role, then ask exactly as the API does -- as the
runtime role ``lattice_app``, through row-level security, with the organization's own
generation configuration and the invocation rule. Refused outside development and test.

``eval answer`` configures each organization's generation provider for each egress
configuration through the audited configuration functions (reason "answer
evaluation"), so it belongs on an evaluation database, never on one with real data.
An external provider (``--generation gemini``) needs ``--allow-external`` and a
synthetic organization: it spends quota and sends synthetic content to a third party.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from lattice.answering.models import Answer, AskRequest
from lattice.answering.service import OFFLINE_GENERATION_DEADLINE_SECONDS, AskDependencies, ask
from lattice.cli_retrieval import (
    GOLDEN,
    REPORTS,
    _databases,
    _fail,
    _member,
    add_compare_arguments,
    compare_into,
)
from lattice.config import Environment, load_provider_settings
from lattice.dataset.distractors import DATA
from lattice.errors import LatticeError
from lattice.evals import baseline, sealed
from lattice.evals.answer import (
    AnswerItemResult,
    GenerationSpec,
    Judge,
    evaluate_answers,
    render_markdown,
)
from lattice.evals.retrieval import config_overrides, load_corpus, load_identities, select_items
from lattice.providers.egress import ProviderRecord
from lattice.providers.factory import PROVIDER_NAMES, build_embedding_provider
from lattice.providers.generation_factory import DefaultGenerationFactory
from lattice.providers.ollama_generation import DEFAULT_ENDPOINT, OllamaGenerationProvider
from lattice.providers.records import GenerationConfig
from lattice.providers.secrets import EnvironmentSecretStore


def _impersonation_allowed() -> bool:
    return os.environ.get("LATTICE_ENV") in ("development", "test")


def _env() -> Environment:
    return Environment(os.environ.get("LATTICE_ENV", "development"))


def _print_answer(answer: Answer) -> None:
    print(f"status: {answer.status}{f' ({answer.status_reason})' if answer.status_reason else ''}")
    if answer.message:
        print(f"  {answer.message}")
    handles = {e.evidence_id: i for i, e in enumerate(answer.evidence, start=1)}
    for claim in answer.claims:
        refs = ",".join(f"[{handles[c.evidence_id]}]" for c in claim.citations)
        print(f"- {claim.text} {refs}")
    for conflict in answer.conflicts:
        print(f"* {conflict.kind}: {conflict.summary}")
    for unknown in answer.unknowns:
        print(f"? {unknown}")
    if answer.clarification:
        choices = ", ".join(c.name for c in answer.clarification.choices)
        print(f"clarify: {answer.clarification.question} {choices}")
    for notice in answer.notices:
        print(f"! {notice.message}")
    print("evidence:")
    for i, e in enumerate(answer.evidence, start=1):
        used = "used" if e.used_for_answer else f"not used ({e.not_used_reason or '-'})"
        when = e.occurred_at.date().isoformat() if e.occurred_at else "-"
        print(
            f"  [{i}] {e.title}{f' {e.display_ref}' if e.display_ref else ''} {when} "
            f"{e.classification} {used}"
        )
    print(f"trace: {answer.trace_id}")


async def ask_command(args: argparse.Namespace) -> int:
    if not _impersonation_allowed():
        return _fail("ask acts as a member: it requires LATTICE_ENV=development or test")
    try:
        request = AskRequest.model_validate(
            {
                "question": args.question,
                "scope": {"account": args.account, "source_types": tuple(args.source_type or ())},
                "classification": args.classification,
            }
        )
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False)
        )
        return _fail(f"invalid question: {problems}")
    deps = AskDependencies(
        embedding=build_embedding_provider(args.embedding, load_provider_settings()),
        generation=DefaultGenerationFactory(_env()),
        secrets=EnvironmentSecretStore(),
        generation_deadline_seconds=OFFLINE_GENERATION_DEADLINE_SECONDS,
    )
    app, owner = _databases("lattice-ask")
    try:
        member = await _member(owner, args.org, args.as_member)
        if member is None:
            return _fail("unknown organization or member")
        try:
            outcome = await ask(
                app, organization_id=member[0], membership_id=member[1], request=request, deps=deps
            )
        except LatticeError as exc:
            return _fail(exc.code.value)
    finally:
        await app.dispose()
        await owner.dispose()
    if args.json:
        print(outcome.answer.model_dump_json(indent=2))
    else:
        _print_answer(outcome.answer)
    return 0


def _progress(r: AnswerItemResult) -> None:
    got = f"{r.status}/{r.status_reason}" if r.status else f"error:{r.error}"
    flag = " SECURITY" if r.violations else ""
    seconds = r.duration_ms / 1000
    print(
        f"  {r.id:<46} {r.persona:<7} {r.egress_config[:7]:<7} {got:<44} "
        f"{len(r.claims)} claims {seconds:5.1f}s{flag}",
        flush=True,
    )


def _spec(args: argparse.Namespace) -> GenerationSpec | str:
    if args.generation == "fake":
        return GenerationSpec("fake", "fake-extractive-v1", "external_api")
    if args.generation == "ollama":
        endpoint = args.endpoint or DEFAULT_ENDPOINT
        try:
            deployment = OllamaGenerationProvider(endpoint=endpoint).deployment
        except ValueError as exc:
            return str(exc)
        return GenerationSpec("ollama", args.model or "qwen3.5:4b", deployment, endpoint)
    if not args.allow_external:
        return (
            "--generation gemini sends synthetic content to Google and spends quota: "
            "pass --allow-external"
        )
    settings = load_provider_settings()
    if settings.gemini_api_key is None:
        return "LATTICE_GEMINI_API_KEY (or GEMINI_API_KEY) is not set"
    reference = "env:GEMINI_API_KEY"
    if os.environ.get("LATTICE_GEMINI_API_KEY"):
        reference = "env:LATTICE_GEMINI_API_KEY"
    paid = settings.gemini_data_use == "paid"
    return GenerationSpec(
        "gemini",
        args.model or "gemini-3.5-flash-lite",
        "external_api",
        credential_ref=reference,
        data_use=settings.gemini_data_use,
        training_opt_out=paid,
        retention_mode="abuse_monitoring_limited" if paid else "unpaid_product_improvement",
    )


async def eval_answer(args: argparse.Namespace) -> int:
    if not _impersonation_allowed():
        return _fail("the evaluation acts as members: it requires LATTICE_ENV=development or test")
    spec = _spec(args)
    if isinstance(spec, str):
        return _fail(spec)
    if args.judge and spec.deployment != "local" and spec.provider != "fake":
        return _fail(
            "--judge is limited to local providers (authored facts may be above the "
            "external egress level)"
        )
    problem = sealed.argument_problem(args)
    if problem:
        return _fail(problem)
    dataset = sealed.load(args.data, args.golden, sealed=args.sealed)
    if dataset.problems:
        return _fail("the dataset does not lint; run `lattice dataset lint`")
    items, problem = sealed.restrict(
        select_items(
            dataset,
            categories=args.category or (),
            personas=args.persona or (),
            item_ids=args.item or (),
        ),
        sealed=args.sealed,
    )
    if problem:
        return _fail(problem)
    try:
        retrieval_config = config_overrides(args.set or ())
    except (TypeError, ValueError) as exc:
        return _fail(str(exc))
    factory = DefaultGenerationFactory(_env())
    embedding = build_embedding_provider(args.embedding, load_provider_settings())
    app, owner = _databases("lattice-eval-answer")
    if args.sealed:
        sealed.record_access(
            args.golden,
            kind="answer",
            reason=args.reason,
            items=items,
            settings={
                "generation": spec.provider,
                "model": spec.model,
                "embedding": args.embedding,
                "retrieval_config": retrieval_config.version,
                "set": args.set or [],
            },
        )
    try:
        identities = await load_identities(owner, dataset)
        corpus = await load_corpus(owner, identities.org_ids)
        judge = Judge(factory.build(_judge_config(spec), None, None)) if args.judge else None
        report = await evaluate_answers(
            app,
            owner,
            dataset,
            identities=identities,
            corpus=corpus,
            spec=spec,
            factory=factory,
            secrets=EnvironmentSecretStore(),
            embedding=embedding,
            items=items,
            judge=judge,
            progress=None if args.quiet else _progress,
            retrieval_config=retrieval_config,
        )
    finally:
        await app.dispose()
        await owner.dispose()
    out = (args.out / "sealed" if args.sealed else args.out) / (
        f"answer-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report.to_json(), indent=2, default=str) + "\n")
    (out / "report.md").write_text(render_markdown(report))
    s = report.summary
    print(
        f"items {s['ran']}/{s['items']}; status accuracy {s['status_accuracy']}; "
        f"statuses {s['statuses']}"
    )
    print(f"generation: {s['generation']}")
    print(f"security: {s['security']}")
    f = s["foundation"]
    print(
        f"context recall {f['context_recall']['macro']} ({f['context_recall']['runs']} runs); "
        f"abstention caught before the model {f['abstention']['catch_before_model']}; "
        f"false abstention {f['abstention']['false_abstention']}"
    )
    print(
        "by split (held_out is reported, never tuned against): "
        + "; ".join(
            f"{name} {b['ran']} runs, status accuracy {b['status_accuracy']}, context recall "
            f"{b['context_recall']['macro']}"
            for name, b in s["by_split"].items()
        )
    )
    if len(s.get("by_held_out_cohort", {})) > 1:
        print(
            "held_out by cohort: "
            + "; ".join(
                f"{name} {b['ran']} runs, context recall {b['context_recall']['macro']}"
                for name, b in s["by_held_out_cohort"].items()
            )
        )
    if s["judge"]:
        print(f"judge (uncalibrated): {s['judge']}")
    if spec.provider == "fake":
        print("note: the fake provider is a test double; these numbers are not answer quality")
    print(f"report: {out}")
    if report.security_failures:
        print("SECURITY FAILURE: see the report", file=sys.stderr)
        return 1
    if args.compare is not None:
        return compare_into(baseline.normalize(report), args, out)
    return 0


def _judge_config(spec: GenerationSpec) -> GenerationConfig:
    return GenerationConfig(
        ProviderRecord(spec.provider, spec.model, spec.deployment, "internal", True),
        spec.endpoint,
        True,
    )


def add_parsers(
    commands: "argparse._SubParsersAction[argparse.ArgumentParser]",
    eval_commands: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    ask_cmd = commands.add_parser("ask", help="Ask a question as a member (dev/test)")
    ask_cmd.add_argument("--org", required=True)
    ask_cmd.add_argument("--as", dest="as_member", required=True, metavar="EMAIL")
    ask_cmd.add_argument("--account")
    ask_cmd.add_argument("--source-type", action="append", choices=["doc", "ticket", "chat"])
    ask_cmd.add_argument("--classification", choices=["internal", "confidential", "restricted"])
    ask_cmd.add_argument("--embedding", choices=list(PROVIDER_NAMES), default="none")
    ask_cmd.add_argument("--json", action="store_true")
    ask_cmd.add_argument("question")

    answer = eval_commands.add_parser("answer", help="Generation metrics over the golden set")
    answer.add_argument("--generation", choices=["fake", "ollama", "gemini"], default="fake")
    answer.add_argument("--model")
    answer.add_argument("--endpoint", help="Ollama endpoint (default loopback)")
    answer.add_argument("--allow-external", action="store_true")
    answer.add_argument("--embedding", choices=list(PROVIDER_NAMES), default="fake")
    answer.add_argument("--judge", action="store_true", help="local providers only; uncalibrated")
    answer.add_argument("--category", action="append")
    answer.add_argument("--persona", action="append")
    answer.add_argument("--item", action="append")
    answer.add_argument("--data", type=Path, default=DATA)
    answer.add_argument("--golden", type=Path, default=GOLDEN)
    answer.add_argument("--out", type=Path, default=REPORTS)
    answer.add_argument("--quiet", action="store_true")
    answer.add_argument(
        "--set",
        action="append",
        metavar="NAME=VALUE",
        help="change one RetrievalConfig tunable for this run (VALUE as JSON); evaluation only",
    )
    add_compare_arguments(answer, required=False)
    sealed.add_arguments(answer)
