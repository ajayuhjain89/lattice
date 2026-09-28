"""``lattice models ...`` and ``lattice provider approve`` (Phase 5 closure).

* ``models list`` -- the pinned local models, their license, size and cache state.
* ``models fetch NAME`` -- the only command that downloads model weights: the pinned
  files, verified by size and SHA-256, into ``LATTICE_MODEL_DIR``. Never implicit.
* ``models verify NAME`` -- re-hash the cached files.
* ``provider approve`` -- set a recorded provider's ``approved_for_level`` for one
  organization: the explicit administrator action of security-model §6, with a stated
  reason, logged as an event. It cannot create a record (a provider is recorded when
  first used) and does not touch the organization's external egress level, which still
  caps every external provider.
"""

import argparse
import logging
import sys

from sqlalchemy import text

from lattice.config import load_migration_settings, load_provider_settings
from lattice.db.engine import Database, create_engine
from lattice.ingestion.store import organization_id
from lattice.log import log_event
from lattice.providers.local_models import (
    MODELS,
    LocalModelUnavailable,
    check_present,
    fetch,
    spec_for,
    verify,
)

_log = logging.getLogger("lattice.providers")
LEVELS = ("internal", "confidential", "restricted")


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def models(args: argparse.Namespace) -> int:
    root = load_provider_settings().model_dir
    if args.models_command == "list":
        for spec in MODELS.values():
            try:
                check_present(spec, root)
                state = "cached"
            except LocalModelUnavailable as exc:
                state = exc.code
            print(
                f"{spec.name}  {spec.identity}  {spec.license}  {spec.dimensions} dims  "
                f"{spec.download_bytes / 1e9:.2f} GB  {state}  ({spec.path(root)})"
            )
        return 0
    try:
        spec = spec_for(args.name)
        if args.models_command == "verify":
            directory = verify(spec, root)
            print(f"{spec.name}: verified ({directory})")
            return 0
        print(
            f"fetching {spec.name} ({spec.repository}@{spec.revision[:12]}, {spec.license}, "
            f"{spec.download_bytes / 1e9:.2f} GB) into {spec.path(root)}"
        )
        last: dict[str, int] = {}

        def progress(name: str, done: int, total: int) -> None:
            step = done * 10 // max(total, 1)
            if last.get(name) != step:
                last[name] = step
                print(f"  {name}: {done * 100 // max(total, 1)}%", flush=True)

        directory = fetch(spec, root, progress=progress)
        print(f"{spec.name}: ready ({directory})")
        return 0
    except LocalModelUnavailable as exc:
        return _fail(str(exc))


async def approve(args: argparse.Namespace) -> int:
    if not args.reason.strip():
        return _fail("--reason is required")
    db = Database(
        create_engine(
            load_migration_settings().migration_database_url.get_secret_value(),
            application_name="lattice-provider",
            pool_size=1,
        )
    )
    try:
        async with db.unscoped_transaction() as s:
            org = await organization_id(s, args.org)
        if org is None:
            return _fail(f"unknown organization: {args.org}")
        async with db.tenant_transaction(org) as s:
            row = (
                await s.execute(
                    text(
                        "UPDATE model_providers SET approved_for_level = :level "
                        "WHERE organization_id = :o AND purpose = 'embedding' "
                        "AND provider = :p AND model = :m "
                        "RETURNING deployment, training_opt_out"
                    ),
                    {"level": args.level, "o": org, "p": args.provider, "m": args.model},
                )
            ).one_or_none()
        if row is None:
            return _fail(
                "no such provider record for this organization (a provider is recorded "
                "when a sync first uses it)"
            )
    finally:
        await db.dispose()
    log_event(
        _log,
        logging.WARNING,
        "provider.approval_changed",
        organization_id=str(org),
        provider=args.provider,
        model=args.model,
        deployment=row[0],
        approved_for_level=args.level,
        reason=args.reason.strip()[:200],
    )
    note = "" if row[0] == "local" else " (still capped by the external egress level)"
    print(f"{args.org}: {args.provider}/{args.model} approved for {args.level}{note}")
    if not row[1]:
        print(
            "  note: this provider may use submitted content; the egress policy sends it "
            "content from synthetic organizations only"
        )
    return 0


def add_parsers(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    models_cmd = commands.add_parser("models", help="Local embedding model weights (ADR-009)")
    models_commands = models_cmd.add_subparsers(dest="models_command", required=True)
    models_commands.add_parser("list", help="Pinned local models and cache state")
    for name, help_text in (
        ("fetch", "Download and verify a pinned model (explicit network access)"),
        ("verify", "Re-hash a cached model"),
    ):
        sub = models_commands.add_parser(name, help=help_text)
        sub.add_argument("name", choices=sorted(MODELS))

    provider_cmd = commands.add_parser("provider", help="Model-provider approval")
    provider_commands = provider_cmd.add_subparsers(dest="provider_command", required=True)
    approve_cmd = provider_commands.add_parser(
        "approve", help="Set a recorded embedding provider's approved level (admin action)"
    )
    approve_cmd.add_argument("--org", required=True)
    approve_cmd.add_argument("--provider", required=True)
    approve_cmd.add_argument("--model", required=True)
    approve_cmd.add_argument("--level", choices=LEVELS, required=True)
    approve_cmd.add_argument("--reason", required=True)
