"""Operator command line: ``lattice <command>``.

    lattice seed --manifest PATH [--manifest PATH ...]
        Upsert organizations/users/memberships from manifests (as lattice_owner).
    lattice check-db-role
        Run the API's startup safety check against LATTICE_DATABASE_URL and report.
    lattice dataset lint [--data DIR] [--golden DIR]
        Validate the synthetic dataset and the golden set (no database, no model).
    lattice dataset generate [--data DIR]
        Regenerate the deterministic bulk distractor tickets.

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
from lattice.dataset.distractors import DATA, write_bulk_tickets
from lattice.dataset.lint import lint, load_dataset, summary
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lattice", description="Lattice operator tools")
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed", help="Seed organizations from YAML manifests")
    seed.add_argument("--manifest", type=Path, action="append", required=True)
    commands.add_parser("check-db-role", help="Verify the runtime database role is safe")
    dataset = commands.add_parser("dataset", help="Synthetic dataset tools")
    dataset_commands = dataset.add_subparsers(dest="dataset_command", required=True)
    lint_cmd = dataset_commands.add_parser("lint", help="Validate the dataset and golden set")
    lint_cmd.add_argument("--data", type=Path, default=DATA)
    lint_cmd.add_argument("--golden", type=Path, default=DATA.parents[1] / "evals" / "golden")
    generate = dataset_commands.add_parser("generate", help="Regenerate bulk distractor tickets")
    generate.add_argument("--data", type=Path, default=DATA)
    args = parser.parse_args(argv)

    if args.command == "dataset":
        if args.dataset_command == "lint":
            return _dataset_lint(args.data, args.golden)
        for path in write_bulk_tickets(args.data):
            print(f"wrote {path}")
        return 0
    try:
        if args.command == "seed":
            return asyncio.run(_seed(args.manifest))
        return asyncio.run(_check_db_role())
    except ConfigurationError as exc:
        return _fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
