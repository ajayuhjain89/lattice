"""``lattice worker``: the background worker process (Phase 8; ADR-013).

    lattice worker [--once]
        Run the jobs administrators request (syncs of their sources) until SIGTERM or
        SIGINT; a job in progress is finished first. --once: run every due job, then
        exit (operators, tests).

Runs as ``lattice_worker`` (LATTICE_WORKER_DATABASE_URL). At startup it refuses to run
with a role that could bypass row-level security, or against a schema at another
revision -- the same checks as the API.
"""

import argparse
import asyncio
import logging
import os
import signal
import sys

from lattice import metrics
from lattice.build import report_startup
from lattice.config import (
    ConfigurationError,
    Environment,
    load_provider_settings,
    load_worker_settings,
    owner_credential_problem,
)
from lattice.db.engine import Database, create_engine
from lattice.db.role_check import verify_runtime_role
from lattice.jobs.worker import Heartbeat, Worker
from lattice.log import configure_logging, scrub_text
from lattice.providers.factory import build_embedding_provider


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    worker = commands.add_parser("worker", help="Run the background worker (requested syncs)")
    worker.add_argument("--once", action="store_true", help="run every due job, then exit")


async def run(args: argparse.Namespace) -> int:
    settings = load_worker_settings()
    if (problem := owner_credential_problem(settings.env, os.environ)) is not None:
        raise ConfigurationError(problem)
    configure_logging(
        level=settings.log_level,
        include_exception_text=settings.env is Environment.DEVELOPMENT,
    )
    db = Database(
        create_engine(
            settings.worker_database_url.get_secret_value(),
            application_name="lattice-worker",
            pool_size=2,
            statement_timeout_ms=settings.db_statement_timeout_ms,
            connect_timeout_seconds=settings.db_connect_timeout_seconds,
        )
    )
    server = None
    try:
        await verify_runtime_role(db.engine)
        embedding = (
            None
            if settings.sync_embedding == "none"
            else build_embedding_provider(settings.sync_embedding, load_provider_settings())
        )
        worker = Worker(
            db,
            embedding=embedding,
            lease_seconds=settings.worker_lease_seconds,
            heartbeat=Heartbeat(settings.worker_heartbeat_file),
        )
        if args.once:
            while await worker.run_once() is not None:
                pass
            return 0
        report_startup("worker", logging.getLogger("lattice.jobs"))
        if settings.metrics_address:
            server = metrics.serve(settings.metrics_address)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await worker.run_forever(stop, poll_seconds=settings.worker_poll_seconds)
        return 0
    except Exception as exc:
        # Startup failed (configuration, role, schema revision, database unreachable):
        # exit non-zero for the supervisor to restart. Credential-shaped text is scrubbed.
        print(f"error: {type(exc).__name__}: {scrub_text(str(exc))}", file=sys.stderr)
        return 1
    finally:
        if server is not None:
            server[0].shutdown()  # type: ignore[attr-defined]
        await db.dispose()
