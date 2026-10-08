"""``lattice secrets new-key|rekey`` and the operator side of recoverable credentials
(phase-12-plan C3.3; runbook "Master keys").

* ``secrets new-key [--id ID]`` prints one new ``id:<base64>`` entry for
  ``LATTICE_SECRET_KEYS``: 32 random bytes. It is the only command that prints key
  material, and it stores nothing.
* ``secrets rekey --reason R [--org SLUG]`` re-encrypts every live ciphertext under
  ``LATTICE_SECRET_ACTIVE_KEY``, one organization per transaction. It refuses before
  writing anything when stored ciphertext uses a key ID this process does not hold, and
  aborts an organization whose ciphertext does not authenticate. Repeatable: a second
  run finds nothing to move. Each organization that changed gets one ``secret.rekeyed``
  event (counts and key IDs, never material). Afterwards the old key can be retired --
  but keep it while backups encrypted under it must stay restorable.

Values read for ``--client-secret-stdin`` / ``--value-stdin`` come from standard input
(without echo on a terminal) and are never printed.
"""

import argparse
import getpass
import os
import secrets
import sys

from pydantic import SecretStr
from sqlalchemy import text

from lattice.access.audit import record_event
from lattice.config import ConfigurationError, load_migration_settings
from lattice.db.engine import Database, create_engine
from lattice.ingestion.store import organization_id
from lattice.vault.keys import Keyring, KeyringError, new_key_line
from lattice.vault.records import RekeyRefused, rekey_organization, stored_key_ids
from lattice.vault.sealing import normalise_value


def operator_keyring() -> Keyring:
    """The master keys in this operator process's environment (possibly none)."""
    try:
        return Keyring.from_environment(os.environ)
    except KeyringError as exc:
        raise ConfigurationError(str(exc)) from None


def read_secret_stdin() -> SecretStr:
    """A secret from standard input: prompted without echo on a terminal, else read to
    the end. ``ValueError`` (without the value) when empty or too long."""
    raw = getpass.getpass("Secret (not echoed): ") if sys.stdin.isatty() else sys.stdin.read()
    return normalise_value(SecretStr(raw))


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def new_key(args: argparse.Namespace) -> int:
    try:
        line = new_key_line(args.id, secrets.token_bytes(32))
    except KeyringError as exc:
        return _fail(str(exc))
    print(line)
    print(
        "Add this entry to LATTICE_SECRET_KEYS for the API and operator processes (never the "
        "worker or the migration job), store it in the platform's secret mechanism and back "
        "it up separately from the database.",
        file=sys.stderr,
    )
    return 0


async def rekey(args: argparse.Namespace) -> int:
    if not args.reason.strip():
        return _fail("--reason is required")
    try:
        keyring = operator_keyring()
    except ConfigurationError as exc:
        return _fail(str(exc))
    if keyring.active is None:
        return _fail("LATTICE_SECRET_KEYS and LATTICE_SECRET_ACTIVE_KEY are not set")
    actor = f"operator:{os.environ.get('USER', 'cli')}"
    db = Database(
        create_engine(
            load_migration_settings().migration_database_url.get_secret_value(),
            application_name="lattice-secrets",
            pool_size=1,
        )
    )
    try:
        async with db.unscoped_transaction() as s:
            unknown = sorted(await stored_key_ids(s) - keyring.ids)
            if args.org:
                found = await organization_id(s, args.org)
                if found is None:
                    return _fail(f"unknown organization: {args.org}")
                orgs = [(found, args.org)]
            else:
                rows = await s.execute(text("SELECT id, slug FROM organizations ORDER BY slug"))
                orgs = [(r.id, str(r.slug)) for r in rows]
        if unknown:
            return _fail(
                f"stored ciphertext uses key ID(s) {', '.join(unknown)} that this process does "
                "not hold; list them in LATTICE_SECRET_KEYS (decrypt-only) and run again. "
                "Nothing was re-keyed"
            )
        total = 0
        for org, slug in orgs:
            try:
                async with db.tenant_transaction(org) as s:
                    counts = await rekey_organization(s, keyring, org)
                    if counts.rekeyed:
                        await record_event(
                            s,
                            org,
                            actor=actor,
                            action="secret.rekeyed",
                            target={"organization_id": str(org)},
                            before={"key_ids": sorted(counts.from_keys)},
                            after={
                                "key_id": keyring.active,
                                "rekeyed": counts.rekeyed,
                                "already_current": counts.already_current,
                            },
                            reason=args.reason,
                        )
            except RekeyRefused as exc:
                print(f"{slug}: refused -- {exc}", file=sys.stderr)
                return 1
            total += counts.rekeyed
            if counts.rekeyed or counts.already_current:
                print(
                    f"{slug}: re-keyed {counts.rekeyed}, already on {keyring.active} "
                    f"{counts.already_current}"
                )
        print(f"done: {total} credential(s) re-keyed to {keyring.active}")
    finally:
        await db.dispose()
    return 0


def add_parsers(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    secrets_cmd = commands.add_parser(
        "secrets", help="Master keys for encrypted credentials (C3.3)"
    )
    secrets_commands = secrets_cmd.add_subparsers(dest="secrets_command", required=True)
    key = secrets_commands.add_parser("new-key", help="Print a new key entry (stores nothing)")
    key.add_argument("--id", required=True, help="the key ID, e.g. k2026-10")
    rekey_cmd = secrets_commands.add_parser(
        "rekey", help="Re-encrypt every stored credential under the active key (audited)"
    )
    rekey_cmd.add_argument("--org", help="one organization (default: all)")
    rekey_cmd.add_argument("--reason", required=True)
