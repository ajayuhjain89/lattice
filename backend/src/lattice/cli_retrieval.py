"""``lattice search`` and ``lattice eval retrieval`` (Phase 5).

Both act *as a member*: they resolve a persona's membership (operator lookup, as
lattice_owner) and then retrieve exactly as the product does -- as the runtime role
``lattice_app``, through row-level security, with the member's authorization context
resolved in the request's snapshot. Because they impersonate a member they are refused
outside development and test, like the seed.

``search`` prints that member's permitted evidence (titles and, with ``--text``, chunk
text). ``eval retrieval`` prints metrics and record keys, never chunk text, and writes
``report.json`` + ``report.md`` under ``evals/reports/<timestamp>/`` (not committed).
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import text

from lattice.config import load_migration_settings, load_provider_settings, load_settings
from lattice.dataset.distractors import DATA
from lattice.dataset.lint import load_dataset
from lattice.db.engine import Database, create_engine
from lattice.errors import LatticeError
from lattice.evals.retrieval import (
    ItemResult,
    evaluate_retrieval,
    render_markdown,
    select_items,
)
from lattice.providers.factory import PROVIDER_NAMES, build_embedding_provider
from lattice.retrieval import RetrievalQuery, RetrievalResult, retrieve

REPORTS = DATA.parents[1] / "evals" / "reports"
GOLDEN = DATA.parents[1] / "evals" / "golden"


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _impersonation_allowed() -> bool:
    return os.environ.get("LATTICE_ENV") in ("development", "test")


def _databases(name: str) -> tuple[Database, Database]:
    app = Database(
        create_engine(
            load_settings().database_url.get_secret_value(), application_name=name, pool_size=2
        )
    )
    owner = Database(
        create_engine(
            load_migration_settings().migration_database_url.get_secret_value(),
            application_name=f"{name}-lookup",
            pool_size=1,
        )
    )
    return app, owner


async def _member(owner: Database, org: str, email: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    async with owner.unscoped_transaction() as s:
        org_id = (
            await s.execute(text("SELECT id FROM organizations WHERE slug = :s"), {"s": org})
        ).scalar_one_or_none()
    if org_id is None:
        return None
    async with owner.tenant_transaction(org_id) as s:
        membership = (
            await s.execute(
                text(
                    "SELECT m.id FROM memberships m JOIN users u ON u.id = m.user_id "
                    "WHERE u.email = :e"
                ),
                {"e": email},
            )
        ).scalar_one_or_none()
    return None if membership is None else (org_id, membership)


def _print_result(result: RetrievalResult, *, show_text: bool, show_pool: bool) -> None:
    s = result.semantic
    print(
        f"semantic: {s.status}{f' ({s.reason})' if s.reason else ''}; "
        f"identifiers: {list(result.identifiers) or '-'}; "
        f"accounts mentioned: {[a.key for a in result.account_mentions] or '-'}; "
        f"temporal intent: {result.temporal_intent}"
    )
    print(f"counts: {result.trace.counts}; ms: {result.trace.timings_ms}")
    for e in result.evidence:
        ranks = " ".join(f"{c[:3]}#{r}" for c, r in e.channel_ranks.items())
        where = f" {e.display_ref}" if e.display_ref else ""
        anchors = ",".join(e.source_anchors[:4])
        account = f" [{e.account.name}]" if e.account else ""
        when = e.occurred_at.date().isoformat() if e.occurred_at else "-"
        print(
            f"{e.rank:>2}. {e.final_score:.5f} {ranks:<24} {e.source_type:<6} {when} "
            f"{e.title}{where} #{anchors}{account}"
        )
        if show_text:
            print("      " + e.text[:400].replace("\n", "\n      "))
    if show_pool:
        print("reranked pool (chunk, matched_by, rrf, features, final):")
        for r in result.trace.reranked:
            print(
                f"  {r.rank:>2}. {r.chunk_id} {'+'.join(r.matched_by):<24} {r.rrf_score:.5f} "
                f"{r.features} {r.final_score:.5f}"
            )


async def search(args: argparse.Namespace) -> int:
    if not _impersonation_allowed():
        return _fail("search acts as a member: it requires LATTICE_ENV=development or test")
    try:
        query = RetrievalQuery.model_validate(
            {
                "text": args.question,
                "scope": {
                    "account": args.account,
                    "source_types": tuple(args.source_type or ()),
                    "since": args.since,
                    "until": args.until,
                },
                "limit": args.limit,
                "classification": args.classification,
            }
        )
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False)
        )
        return _fail(f"invalid query: {problems}")
    provider = build_embedding_provider(args.embedding, load_provider_settings())
    app, owner = _databases("lattice-search")
    try:
        member = await _member(owner, args.org, args.as_member)
        if member is None:
            return _fail("unknown organization or member")
        try:
            result = await retrieve(
                app,
                organization_id=member[0],
                membership_id=member[1],
                query=query,
                provider=provider,
            )
        except LatticeError as exc:
            detail = ", ".join(f"{d.field}: {d.issue}" for d in exc.details or [])
            return _fail(f"{exc.code.value}{f' ({detail})' if detail else ''}")
    finally:
        await app.dispose()
        await owner.dispose()
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        _print_result(result, show_text=args.text, show_pool=args.pool)
    return 0


def _progress(result: ItemResult) -> None:
    r10 = result.recall.get("@10")
    flag = " SECURITY" if result.violations else ""
    status = result.error or result.semantic_status
    recall = "-" if r10 is None else f"{r10:.2f}"
    print(f"  {result.id:<48} {result.persona:<7} R@10 {recall:>4} {status}{flag}", flush=True)


async def eval_retrieval(args: argparse.Namespace) -> int:
    if not _impersonation_allowed():
        return _fail("the evaluation acts as members: it requires LATTICE_ENV=development or test")
    dataset = load_dataset(args.data, args.golden)
    if dataset.problems:
        return _fail("the dataset does not lint; run `lattice dataset lint`")
    items = select_items(
        dataset,
        categories=args.category or (),
        personas=args.persona or (),
        item_ids=args.item or (),
    )
    provider = build_embedding_provider(args.embedding, load_provider_settings())
    app, owner = _databases("lattice-eval")
    try:
        report = await evaluate_retrieval(
            app, owner, dataset, provider, items=items, progress=None if args.quiet else _progress
        )
    finally:
        await app.dispose()
        await owner.dispose()
    out = args.out / datetime.now().strftime("%Y%m%dT%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report.to_json(), indent=2, default=str) + "\n")
    (out / "report.md").write_text(render_markdown(report))
    s = report.summary
    o = s["overall"]
    print(
        f"items {o['items']} (with targets {o['items_with_targets']}): "
        f"R@1 {o['recall@1']} R@5 {o['recall@5']} R@10 {o['recall@10']} "
        f"R@20 {o['recall@20']} MRR {o['mrr']}"
    )
    print(f"security: {s['security']}")
    print(f"semantic: {s['semantic_outcomes']}; misses: {s['miss_causes_top10']}")
    if report.provider_usage:
        print(f"query embedding usage: {report.provider_usage}")
    if report.embedding_provider == "fake":
        print("note: the fake provider is a test double; recall here is not semantic quality")
    print(f"report: {out}")
    if report.security_failures:
        print("SECURITY FAILURE: see the report", file=sys.stderr)
        return 1
    return 0


def add_parsers(
    commands: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> "argparse._SubParsersAction[argparse.ArgumentParser]":
    """Registers ``search`` and ``eval retrieval``; returns the ``eval`` subcommands."""
    embedding_choices = list(PROVIDER_NAMES)
    search_cmd = commands.add_parser("search", help="Retrieve evidence as a member (dev/test)")
    search_cmd.add_argument("--org", required=True)
    search_cmd.add_argument("--as", dest="as_member", required=True, metavar="EMAIL")
    search_cmd.add_argument("--account")
    search_cmd.add_argument("--source-type", action="append", choices=["doc", "ticket", "chat"])
    search_cmd.add_argument("--since")
    search_cmd.add_argument("--until")
    search_cmd.add_argument("--limit", type=int, default=10)
    search_cmd.add_argument(
        "--classification",
        choices=["internal", "confidential", "restricted"],
        help="declare the question more sensitive than the organization default (D-13)",
    )
    search_cmd.add_argument("--embedding", choices=embedding_choices, default="fake")
    search_cmd.add_argument("--text", action="store_true", help="print chunk text")
    search_cmd.add_argument("--pool", action="store_true", help="print the reranked pool")
    search_cmd.add_argument("--json", action="store_true")
    search_cmd.add_argument("question")

    evals = commands.add_parser("eval", help="Evaluation runners")
    eval_commands = evals.add_subparsers(dest="eval_command", required=True)
    retrieval = eval_commands.add_parser("retrieval", help="Retrieval metrics over the golden set")
    retrieval.add_argument("--embedding", choices=embedding_choices, default="fake")
    retrieval.add_argument("--category", action="append")
    retrieval.add_argument("--persona", action="append")
    retrieval.add_argument("--item", action="append")
    retrieval.add_argument("--data", type=Path, default=DATA)
    retrieval.add_argument("--golden", type=Path, default=GOLDEN)
    retrieval.add_argument("--out", type=Path, default=REPORTS)
    retrieval.add_argument("--quiet", action="store_true")
    return eval_commands
