"""Deployment commands (Phase 11, P11.2; deployment.md §9).

    lattice version [--json]
        This build's revision, version and schema revision (no database, no secret).
    lattice check-config api|worker|migrate
        Validate a process's configuration from its environment without connecting
        anywhere: every setting, the production guards, and (api, worker) that the schema
        owner's credential is absent. Prints variable names and reasons, never values.
    lattice migrate [--lock-wait-seconds S] [--lock-timeout-ms MS] [--statement-timeout-ms MS]
        The production migration (lattice.db.migrate): owner check, migration lock,
        connection fence, pre-flight (no api/worker session), upgrade, exact verification.
    lattice migrate --status [--json]
        Read-only: database revision, this build's revision, pending migrations, open
        application sessions and whether a migration holds the lock.
    lattice migrate --release-fence
        Give CONNECT back to the application roles after a killed migration.
    lattice migrate --downgrade-to REV [--check]
        Undo this build's newest revisions down to REV, only if each declares a data-safe
        downgrade in its header (D-23: otherwise roll forward); same lock, fence and
        pre-flight as an upgrade. --check: only decide whether it would be allowed.
    lattice check-deployment [--api-instances N] [--api-pool-size N] [--worker-instances N]
                             [--write-privilege-snapshot PATH]
        Read-only database checks (lattice.db.deployment); exit 1 on any failure.

``migrate`` and ``check-deployment`` use the migration credential
(``LATTICE_MIGRATION_DATABASE_URL``).
"""

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from lattice.build import build_info
from lattice.config import (
    ConfigurationError,
    load_migration_settings,
    load_provider_settings,
    load_settings,
    load_worker_settings,
    owner_credential_problem,
)
from lattice.db.deployment import Plan, check_deployment, render_snapshot, snapshot_of
from lattice.db.migrate import (
    MigrationRefused,
    database_status,
    migrate,
    migration_headers,
    release_fence,
    undo_plan,
)
from lattice.log import scrub_text

PROCESSES = ("api", "worker", "migrate")


def add_parsers(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    version = commands.add_parser("version", help="This build's revision and schema revision")
    version.add_argument("--json", action="store_true")

    check_config = commands.add_parser(
        "check-config", help="Validate a process's configuration (no connection)"
    )
    check_config.add_argument("process", choices=PROCESSES)

    migrate_cmd = commands.add_parser("migrate", help="Production migration (stop-the-world)")
    mode = migrate_cmd.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="report only; change nothing")
    mode.add_argument(
        "--release-fence", action="store_true", help="give CONNECT back after a killed run"
    )
    mode.add_argument("--downgrade-to", metavar="REV", help="undo reversible revisions to REV")
    migrate_cmd.add_argument("--json", action="store_true", help="with --status")
    migrate_cmd.add_argument(
        "--check", action="store_true", help="with --downgrade-to: decide only, change nothing"
    )
    migrate_cmd.add_argument("--lock-wait-seconds", type=float, default=60.0)
    migrate_cmd.add_argument("--lock-timeout-ms", type=int, default=5_000)
    migrate_cmd.add_argument("--statement-timeout-ms", type=int, default=600_000)

    check = commands.add_parser("check-deployment", help="Read-only database deployment checks")
    check.add_argument("--api-instances", type=int, default=2)
    check.add_argument("--api-pool-size", type=int, default=5)
    check.add_argument("--worker-instances", type=int, default=1)
    check.add_argument(
        "--write-privilege-snapshot",
        type=Path,
        help="write this database's privilege snapshot to PATH instead of checking",
    )


def version(args: argparse.Namespace) -> int:
    info = build_info()
    if args.json:
        print(json.dumps(info.__dict__, sort_keys=True))
    else:
        print(f"revision={info.revision} version={info.version} schema={info.schema_revision}")
    return 0


def check_config(args: argparse.Namespace) -> int:
    process: str = args.process
    if process == "api":
        settings = load_settings()
        load_provider_settings()
        env = settings.env
    elif process == "worker":
        worker = load_worker_settings()
        load_provider_settings()
        env = worker.env
    else:
        load_migration_settings()
        print("OK: migrate configuration is valid")
        return 0
    if (problem := owner_credential_problem(env, os.environ)) is not None:
        raise ConfigurationError(problem)
    print(f"OK: {process} configuration is valid (LATTICE_ENV={env.value})")
    return 0


def _owner_url() -> str:
    return load_migration_settings().migration_database_url.get_secret_value()


async def migrate_command(args: argparse.Namespace) -> int:
    url = _owner_url()
    try:
        if args.status:
            status, sessions, locked = await database_status(url)
            headers = migration_headers()
            report = {
                "database_revision": status.database_revision,
                "build_revision": status.build_revision,
                "state": status.state,
                "pending": list(status.pending),
                "pending_irreversible": [r for r in status.pending if not headers[r].reversible],
                "application_sessions": sum(s.count for s in sessions),
                "migration_running": bool(locked),
            }
            if args.json:
                print(json.dumps(report, sort_keys=True))
            else:
                for key, value in report.items():
                    print(f"{key}: {value}")
            return 0
        if args.check:
            if args.downgrade_to is None:
                raise MigrationRefused("--check applies to --downgrade-to")
            status, _, _ = await database_status(url)
            undone = undo_plan(status, args.downgrade_to, migration_headers())
            print(f"allowed: would undo {', '.join(undone)} (all declared reversible)")
            return 0
        if args.release_fence:
            can_connect = await release_fence(url)
            print(f"fence released: {', '.join(can_connect) or 'no role'} may connect")
            return 0
        outcome = await migrate(
            url,
            lock_wait_seconds=args.lock_wait_seconds,
            lock_timeout_ms=args.lock_timeout_ms,
            statement_timeout_ms=args.statement_timeout_ms,
            downgrade_to=args.downgrade_to,
        )
    except MigrationRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    except (OSError, SQLAlchemyError) as exc:
        # The migration's transaction rolled back (the schema is unchanged) and the fence
        # was lifted; credential-shaped text is scrubbed.
        print(
            f"FAILED: {type(exc).__name__}: {scrub_text(str(exc)).splitlines()[0][:300]} "
            "(the migration transaction rolled back)",
            file=sys.stderr,
        )
        return 1
    for note in outcome.notes:
        print(f"note: {note}")
    applied = " -> ".join(outcome.applied) or "none"
    print(
        f"OK: {outcome.before or 'empty'} -> {outcome.after} (applied: {applied}) "
        f"in {outcome.seconds:.1f} s"
    )
    return 0


async def check_deployment_command(args: argparse.Namespace) -> int:
    url = _owner_url()
    if args.write_privilege_snapshot is not None:
        args.write_privilege_snapshot.write_text(render_snapshot(await snapshot_of(url)))
        print(f"wrote {args.write_privilege_snapshot}")
        return 0
    plan = Plan(
        api_instances=args.api_instances,
        api_pool_size=args.api_pool_size,
        worker_instances=args.worker_instances,
    )
    checks = await check_deployment(url, plan)
    for check in checks:
        print(f"{'PASS' if check.ok else 'FAIL'}  {check.name}: {check.detail}")
    failed = sum(not c.ok for c in checks)
    print(f"{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0
