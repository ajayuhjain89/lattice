"""Operator command line: ``lattice <command>``.

    lattice seed --manifest PATH [--manifest PATH ...] [--require-synthetic]
        Upsert organizations/users/memberships from manifests (as lattice_owner).
        --require-synthetic: refuse unless every manifest declares data_origin: synthetic
        (the deployment bootstrap of a verification stack; Phase 11).
    lattice check-db-role
        Run the API's startup safety check against LATTICE_DATABASE_URL and report.
    lattice dataset lint [--data DIR] [--golden DIR]
        Validate the synthetic dataset and the golden set (no database, no model).
    lattice dataset generate [--data DIR]
        Regenerate the deterministic bulk distractor tickets.
    lattice sync --org SLUG --sources FILE [--source KEY ...] [--mode full|acl_only]
                 [--dry-run] [--allow-mass-deletion] [--embedding none|fake|local|gemini|openai]
        Apply each source's Lattice configuration from FILE and sync it (ingestion).
    lattice reindex --org SLUG --sources FILE [--source KEY ...] [--rechunk] [--reembed]
                    [--embedding none|fake|local|gemini|openai]
        Re-chunk and/or re-embed a source's documents; never deletes documents.
    lattice source disconnect --org SLUG --source KEY
        Make a source's content unretrievable at once, then purge it.
    lattice apply-admin-grants --org SLUG --grants FILE [--dry-run] [--prune]
        Apply Lattice administrator grants (D-12), after ingestion.
    lattice search --org SLUG --as EMAIL [--account KEY] [--source-type T ...]
                   [--embedding none|fake|local|gemini|openai] [--text] [--pool] [--json] QUESTION
        Retrieve permitted evidence as that member (development/test only).
    lattice eval retrieval [--embedding none|fake|local|gemini|openai] [--category C ...]
                           [--persona P ...] [--item ID ...] [--out DIR]
        Retrieval metrics over the golden set (development/test only).
    lattice ask --org SLUG --as EMAIL [--account KEY] [--classification L] [--json] QUESTION
        Ask as that member (development/test only): retrieval, the invocation rule, the
        organization's own generation provider, validation; prints the answer.
    lattice eval answer [--generation fake|ollama|gemini] [--model M] [--embedding ...]
                        [--judge] [--category C ...] [--persona P ...] [--item ID ...]
        Generation and security metrics over the golden set (development/test only).
    lattice models list | fetch NAME | verify NAME
        Pinned local embedding models: cache state, explicit download, re-hash.
    lattice provider approve|configure|enable|disable|credential|list ...
    lattice org egress --org SLUG [--external-level L] [--organization-controlled-level L]
        Organization provider configuration and AI egress ceilings (administrative;
        each change needs --reason and is recorded in audit_events).
    lattice sso|scim|member|session ..., lattice org suspend|reactivate --org SLUG
        Identity and incident controls (lattice.cli_identity; audited).
    lattice org create --slug S --name N --issuer URL --client-id ID ... --reason R
        Create an organization's trust root: organization, settings, OIDC connection and
        first administrator, in one audited transaction (lattice.cli_org; D-28).
    lattice outbound allow|revoke|list --org SLUG ...
        Private outbound targets registered for one organization and purpose (C4).
    lattice secrets new-key --id ID | rekey --reason R [--org SLUG]
        Master keys for encrypted credentials: a new key entry; re-encrypt under the
        active key (lattice.cli_secrets; C3.3).
    lattice worker [--once]
        The background worker: runs the syncs administrators request from the web app,
        as lattice_worker (LATTICE_WORKER_DATABASE_URL).
    lattice smoke --url ORIGIN [--instance URL ...] [--worker-metrics URL] [--ca-file F]
                  [--production]
        Read-only check that a deployment is usable (lattice.smoke); no database access.
    lattice version | check-config PROCESS | migrate [...] | check-deployment [...]
        Deployment commands (lattice.cli_deploy): build identity, configuration
        validation, the production migration and the database deployment checks.

Ingestion commands run as lattice_owner (operator tools, through row-level security)
and print counts and fixed codes only -- never record content, titles or filenames.

Exit codes: 0 success; 1 check failed, a sync failed or was aborted, or admin grants
were not applied, or a retrieval evaluation found a security violation; 2
configuration or input error; 3 a sync completed as ``partial`` (records rejected,
embeddings failed or the deletion guard tripped -- details printed).
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from lattice import (
    cli_answer,
    cli_deploy,
    cli_identity,
    cli_org,
    cli_providers,
    cli_retrieval,
    cli_secrets,
    cli_worker,
    smoke,
)
from lattice.admin_grants import apply_admin_grants, load_admin_grants
from lattice.config import (
    ConfigurationError,
    load_migration_settings,
    load_provider_settings,
    load_settings,
)
from lattice.dataset.distractors import DATA, write_bulk_tickets
from lattice.dataset.lint import lint, load_dataset, summary
from lattice.db.engine import Database, create_engine
from lattice.db.role_check import inspect_runtime_role
from lattice.ingestion.lifecycle import SourceNotFound, disconnect_source
from lattice.ingestion.store import organization_id
from lattice.ingestion.sync import SyncError, SyncOptions, SyncResult, sync_source
from lattice.providers.factory import PROVIDER_NAMES, ProviderName, build_embedding_provider
from lattice.seed import load_manifest, seed_manifest
from lattice.sources.config import load_sources_file


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


async def _seed(paths: Sequence[Path], *, require_synthetic: bool = False) -> int:
    if os.environ.get("LATTICE_ENV") not in ("development", "test"):
        return _fail(
            "seeding synthetic data requires LATTICE_ENV=development or test "
            "(it is refused in production and when LATTICE_ENV is unset)"
        )
    manifests = []
    for path in paths:
        try:
            manifests.append(load_manifest(path))
        except FileNotFoundError:
            return _fail(f"manifest not found: {path}")
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in exc.errors(include_input=False, include_url=False)
            )
            return _fail(f"invalid manifest {path}: {problems}")
    if require_synthetic:
        for path, manifest in zip(paths, manifests, strict=True):
            if manifest.organization.data_origin != "synthetic":
                return _fail(
                    f"{path}: organization {manifest.organization.slug!r} does not declare "
                    "data_origin: synthetic (only synthetic data may bootstrap this deployment)"
                )

    settings = load_migration_settings()
    db = Database(
        create_engine(
            settings.migration_database_url.get_secret_value(),
            application_name="lattice-seed",
            pool_size=1,
        )
    )
    try:
        for manifest in manifests:
            report = await seed_manifest(db, manifest)
            counts = ", ".join(f"{k}={v}" for k, v in sorted(report.counts.items()))
            print(f"seeded {report.organization}: {counts}")
    finally:
        await db.dispose()
    return 0


async def _check_db_role() -> int:
    settings = load_settings()
    engine = create_engine(
        settings.database_url.get_secret_value(), application_name="lattice-check", pool_size=1
    )
    try:
        report = await inspect_runtime_role(engine, forbid_content_writes=True)
    finally:
        await engine.dispose()
    if report.problems:
        print(f"UNSAFE: role {report.role_name!r}:", file=sys.stderr)
        for problem in report.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(
        f"OK: role {report.role_name!r} is not a superuser, has no BYPASSRLS (directly or via "
        "membership), owns no tables, and every application table has forced RLS."
    )
    return 0


def _dataset_lint(data_dir: Path, golden_dir: Path) -> int:
    dataset = load_dataset(data_dir, golden_dir)
    problems = lint(dataset)
    for problem in problems:
        print(problem, file=sys.stderr)
    facts = summary(dataset)
    for name, value in facts.items():
        print(f"{name}: {value}")
    if problems:
        print(f"FAILED: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print("OK: dataset and golden set are valid")
    return 0


def _owner_db(application_name: str) -> Database:
    settings = load_migration_settings()
    return Database(
        create_engine(
            settings.migration_database_url.get_secret_value(),
            application_name=application_name,
            pool_size=2,
        )
    )


def _print_sync(org: str, result: SyncResult) -> None:
    prefix = "would " if result.dry_run else ""
    status = "dry run" if result.dry_run else result.status
    deletions = (
        f"would delete {result.would_delete}"
        if result.dry_run
        else f"deleted {result.deleted}"
        + (f" (guard held back {result.would_delete})" if result.would_delete else "")
    )
    print(
        f"{org}/{result.source} ({result.mode}): {status} -- seen {result.records_seen}, "
        f"{prefix}create {result.created}, {prefix}update {result.updated}, "
        f"{prefix}update access {result.access_updated}, unchanged {result.unchanged}, "
        f"rejected {result.failed}, {prefix}quarantine {result.quarantined}, {deletions}"
        + (f", skipped {result.skipped}" if result.skipped else "")
    )
    if not result.dry_run:
        print(
            f"  chunks: embedded {result.chunks_embedded}, reused {result.embeddings_reused}, "
            f"excluded by AI egress policy {result.chunks_policy_excluded}, "
            f"embedding failures {result.embedding_failures}"
        )
    if result.egress_block:
        print(f"  AI egress policy: the provider may receive nothing ({result.egress_block})")
    if result.deletion_guard_tripped:
        print(f"  DELETION GUARD: {result.error_code}; nothing was deleted", file=sys.stderr)
    elif result.error_code:
        print(f"  error: {result.error_code}", file=sys.stderr)
    for code, count in sorted(result.failure_codes.items()):
        print(f"  failures: {code} x{count}")


async def _sync(args: argparse.Namespace, *, reindex: bool) -> int:
    try:
        sources = load_sources_file(args.sources)
    except FileNotFoundError:
        return _fail(f"sources file not found: {args.sources}")
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False)
        )
        return _fail(f"invalid sources file {args.sources}: {problems}")
    selected = args.source or [s.key for s in sources.sources]
    unknown = [key for key in selected if sources.get(key) is None]
    if unknown:
        return _fail(f"unknown source key(s): {', '.join(unknown)}")
    provider = build_embedding_provider(args.embedding, load_provider_settings())
    if reindex:
        options = SyncOptions(mode="reindex", rechunk=args.rechunk, reembed=args.reembed)
    else:
        options = SyncOptions(
            mode=args.mode, dry_run=args.dry_run, allow_mass_deletion=args.allow_mass_deletion
        )

    db = _owner_db("lattice-sync")
    exit_code = 0
    try:
        async with db.unscoped_transaction() as s:
            org = await organization_id(s, args.org)
        if org is None:
            return _fail(f"unknown organization: {args.org}")
        for key in selected:
            config = sources.get(key)
            assert config is not None  # noqa: S101 - checked above
            path = (args.sources.parent / config.path).resolve()
            try:
                result = await sync_source(db, org, config, path, provider, options)
            except SyncError as exc:
                print(f"{args.org}/{key}: failed -- {exc.code}", file=sys.stderr)
                exit_code = 1
                continue
            _print_sync(args.org, result)
            if result.status in ("failed", "aborted"):
                exit_code = 1
            elif result.status == "partial" and exit_code == 0:
                exit_code = 3
    finally:
        await db.dispose()
    return exit_code


async def _disconnect(args: argparse.Namespace) -> int:
    db = _owner_db("lattice-source")
    try:
        async with db.unscoped_transaction() as s:
            org = await organization_id(s, args.org)
        if org is None:
            return _fail(f"unknown organization: {args.org}")
        try:
            report = await disconnect_source(db, org, args.source)
        except SourceNotFound:
            return _fail(f"unknown source: {args.source}")
    finally:
        await db.dispose()
    print(
        f"{args.org}/{args.source}: disconnected and purged -- documents {report.documents}, "
        f"chunks {report.chunks} (vectors {report.vectors}), source grants "
        f"{report.source_grants}, admin grants {report.admin_grants}, synced groups "
        f"{report.groups}, sync runs {report.sync_runs}, account mappings "
        f"{report.account_mappings}"
    )
    return 0


async def _apply_admin_grants(args: argparse.Namespace) -> int:
    try:
        grants = load_admin_grants(args.grants)
    except FileNotFoundError:
        return _fail(f"grants file not found: {args.grants}")
    except ValidationError:
        return _fail(f"invalid grants file: {args.grants}")
    db = _owner_db("lattice-admin-grants")
    try:
        report = await apply_admin_grants(
            db, args.org, grants, dry_run=args.dry_run, prune=args.prune
        )
    finally:
        await db.dispose()
    if report.problems:
        for problem in report.problems:
            where = "organization" if problem.index < 0 else f"entry #{problem.index + 1}"
            print(f"{args.org}: {where}: {problem.code}", file=sys.stderr)
        print(f"{args.org}: FAILED -- nothing was written", file=sys.stderr)
        return 1
    verb = "would add" if report.dry_run else "added"
    print(
        f"{args.org}: admin grants {verb} {report.added}, unchanged {report.unchanged}, "
        f"{'would remove' if report.dry_run else 'removed'} {report.removed}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lattice", description="Lattice operator tools")
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed", help="Seed organizations from YAML manifests")
    seed.add_argument("--manifest", type=Path, action="append", required=True)
    seed.add_argument("--require-synthetic", action="store_true")
    commands.add_parser("check-db-role", help="Verify the runtime database role is safe")
    dataset = commands.add_parser("dataset", help="Synthetic dataset tools")
    dataset_commands = dataset.add_subparsers(dest="dataset_command", required=True)
    lint_cmd = dataset_commands.add_parser("lint", help="Validate the dataset and golden set")
    lint_cmd.add_argument("--data", type=Path, default=DATA)
    lint_cmd.add_argument("--golden", type=Path, default=DATA.parents[1] / "evals" / "golden")
    generate = dataset_commands.add_parser("generate", help="Regenerate bulk distractor tickets")
    generate.add_argument("--data", type=Path, default=DATA)

    embedding_choices = list(PROVIDER_NAMES)
    sync = commands.add_parser("sync", help="Sync sources (ingestion)")
    reindex = commands.add_parser("reindex", help="Re-chunk / re-embed a source's documents")
    for command in (sync, reindex):
        command.add_argument("--org", required=True)
        command.add_argument("--sources", type=Path, required=True)
        command.add_argument("--source", action="append")
        command.add_argument("--embedding", choices=embedding_choices, default="none")
    sync.add_argument("--mode", choices=["full", "acl_only"], default="full")
    sync.add_argument("--dry-run", action="store_true")
    sync.add_argument("--allow-mass-deletion", action="store_true")
    reindex.add_argument("--rechunk", action="store_true")
    reindex.add_argument("--reembed", action="store_true")

    source = commands.add_parser("source", help="Source lifecycle")
    source_commands = source.add_subparsers(dest="source_command", required=True)
    disconnect = source_commands.add_parser("disconnect", help="Disconnect and purge a source")
    disconnect.add_argument("--org", required=True)
    disconnect.add_argument("--source", required=True)

    grants = commands.add_parser("apply-admin-grants", help="Apply Lattice admin grants (D-12)")
    grants.add_argument("--org", required=True)
    grants.add_argument("--grants", type=Path, required=True)
    grants.add_argument("--dry-run", action="store_true")
    grants.add_argument("--prune", action="store_true")
    cli_answer.add_parsers(commands, cli_retrieval.add_parsers(commands))
    cli_providers.add_parsers(commands)
    cli_identity.add_parsers(commands)
    cli_org.add_parsers(commands)
    cli_secrets.add_parsers(commands)
    cli_worker.add_parser(commands)
    smoke.add_parser(commands)
    cli_deploy.add_parsers(commands)
    args = parser.parse_args(argv)

    if args.command == "dataset":
        if args.dataset_command == "lint":
            return _dataset_lint(args.data, args.golden)
        for path in write_bulk_tickets(args.data):
            print(f"wrote {path}")
        return 0
    try:
        if args.command == "seed":
            return asyncio.run(_seed(args.manifest, require_synthetic=args.require_synthetic))
        if args.command in ("sync", "reindex"):
            embedding: ProviderName = args.embedding
            args.embedding = embedding
            return asyncio.run(_sync(args, reindex=args.command == "reindex"))
        if args.command == "source":
            return asyncio.run(_disconnect(args))
        if args.command == "apply-admin-grants":
            return asyncio.run(_apply_admin_grants(args))
        if args.command == "search":
            return asyncio.run(cli_retrieval.search(args))
        if args.command == "ask":
            return asyncio.run(cli_answer.ask_command(args))
        if args.command == "eval" and args.eval_command == "answer":
            return asyncio.run(cli_answer.eval_answer(args))
        if args.command == "eval":
            return asyncio.run(cli_retrieval.eval_retrieval(args))
        if args.command == "models":
            return cli_providers.models(args)
        if args.command == "org" and args.org_command == "create":
            return asyncio.run(cli_org.create(args))
        if args.command == "org" and args.org_command in ("suspend", "reactivate"):
            return asyncio.run(cli_identity.run(args))
        if args.command == "outbound":
            return asyncio.run(cli_org.outbound(args))
        if args.command == "secrets" and args.secrets_command == "new-key":
            return cli_secrets.new_key(args)
        if args.command == "secrets":
            return asyncio.run(cli_secrets.rekey(args))
        if args.command in ("provider", "org"):
            return asyncio.run(cli_providers.provider(args))
        if args.command == "worker":
            return asyncio.run(cli_worker.run(args))
        if args.command == "smoke":
            return smoke.run(args)
        if args.command == "version":
            return cli_deploy.version(args)
        if args.command == "check-config":
            return cli_deploy.check_config(args)
        if args.command == "migrate":
            return asyncio.run(cli_deploy.migrate_command(args))
        if args.command == "check-deployment":
            return asyncio.run(cli_deploy.check_deployment_command(args))
        if args.command == "verify-restore":
            return asyncio.run(cli_deploy.verify_restore_command(args))
        if args.command in cli_identity.COMMANDS:
            return asyncio.run(cli_identity.run(args))
        return asyncio.run(_check_db_role())
    except ConfigurationError as exc:
        return _fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
