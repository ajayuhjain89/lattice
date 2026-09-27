"""Operator command line: ``lattice <command>``.

    lattice seed --manifest PATH [--manifest PATH ...]
        Upsert organizations/users/memberships from manifests (as lattice_owner).
    lattice check-db-role
        Run the API's startup safety check against LATTICE_DATABASE_URL and report.

Exit codes: 0 success, 1 check failed, 2 configuration or input error.
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from lattice.config import ConfigurationError, load_migration_settings, load_settings
from lattice.db.engine import Database, create_engine
from lattice.db.role_check import inspect_runtime_role
from lattice.seed import load_manifest, seed_manifest


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


async def _seed(paths: Sequence[Path]) -> int:
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
        report = await inspect_runtime_role(engine)
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lattice", description="Lattice operator tools")
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed", help="Seed organizations from YAML manifests")
    seed.add_argument("--manifest", type=Path, action="append", required=True)
    commands.add_parser("check-db-role", help="Verify the runtime database role is safe")
    args = parser.parse_args(argv)

    try:
        if args.command == "seed":
            return asyncio.run(_seed(args.manifest))
        return asyncio.run(_check_db_role())
    except ConfigurationError as exc:
        return _fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
