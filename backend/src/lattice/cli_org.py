"""``lattice org create`` and ``lattice outbound allow|revoke|list`` (Phase 12, P12.1;
D-28; phase-12-plan C1, C4). Operator commands, as ``lattice_owner`` through row-level
security, each with a reason and audit events; nothing here is reachable from the API.

    lattice org create --slug S --name N --issuer URL --client-id ID
        [--client-secret-ref env:LATTICE_OIDC_SECRET_<NAME> | --client-secret-stdin]
        [--subject-claim sub] [--register-private-issuer]
        --first-admin-email E --first-admin-identifier V [--first-admin-name NAME]
        --reason R
    lattice outbound allow --org SLUG --url URL --purpose oidc|generation --reason R
    lattice outbound revoke --org SLUG --id ID --reason R
    lattice outbound list --org SLUG

Output is safe to paste into a ticket: identifiers, URLs and labels -- never a secret.
"""

import argparse
import os
import sys
import uuid

from lattice.cli_secrets import operator_keyring, read_secret_stdin
from lattice.config import ConfigurationError, load_migration_settings
from lattice.db.engine import Database, create_engine
from lattice.ingestion.store import organization_id
from lattice.orgs import outbound_targets
from lattice.orgs.bootstrap import BootstrapRejected, OrgCreated, OrgRequest, create_organization


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _owner(name: str) -> Database:
    return Database(
        create_engine(
            load_migration_settings().migration_database_url.get_secret_value(),
            application_name=name,
            pool_size=1,
        )
    )


def _actor() -> str:
    return f"operator:{os.environ.get('USER', 'cli')}"


def _origin() -> str:
    return os.environ.get("LATTICE_PUBLIC_URL", "http://127.0.0.1:3000").rstrip("/")


def print_created(result: OrgCreated) -> None:
    origin = _origin()
    state = "created" if result.created else "already exists, unchanged"
    print(f"{result.slug}: organization {state} ({result.organization_id})")
    print(f"  name:              {result.name}")
    kind = {
        "encrypted": "confidential, secret encrypted at rest",
        "reference": "confidential, secret by environment reference",
        "none": "public, PKCE",
    }[result.client_secret]
    print(f"  issuer:            {result.issuer}")
    print(f"  client ID:         {result.client_id} ({kind})")
    print(f"  identifying claim: {result.subject_claim}")
    print(f"  first admin:       {result.admin_email} (administrator, least access otherwise)")
    print(f"  sign-in URL:       {origin}/sso/{result.login_key}")
    print(f"  redirect URI:      {origin}/api/v1/auth/oidc/{result.login_key}/callback")
    print("Register the redirect URI with the identity provider; the first administrator's")
    print("first sign-in verifies the connection.")


async def create(args: argparse.Namespace) -> int:
    try:
        keyring = operator_keyring()
        secret = read_secret_stdin() if args.client_secret_stdin else None
    except ConfigurationError as exc:
        return _fail(str(exc))
    except ValueError as exc:
        return _fail(f"--client-secret-stdin: {exc}")
    request = OrgRequest(
        slug=args.slug,
        name=args.name,
        issuer=args.issuer,
        client_id=args.client_id,
        subject_claim=args.subject_claim,
        admin_email=args.first_admin_email,
        admin_identifier=args.first_admin_identifier,
        admin_name=args.first_admin_name or "",
        reason=args.reason,
        client_secret_ref=args.client_secret_ref,
        client_secret=secret,
        register_private_issuer=args.register_private_issuer,
    )
    db = _owner("lattice-org-create")
    try:
        result = await create_organization(db, request, keyring=keyring, actor=_actor())
    except BootstrapRejected as exc:
        return _fail(str(exc))
    finally:
        await db.dispose()
    print_created(result)
    return 0


async def outbound(args: argparse.Namespace) -> int:
    reason = getattr(args, "reason", None)
    if reason is not None and not reason.strip():
        return _fail("--reason is required")
    db = _owner("lattice-outbound")
    try:
        async with db.unscoped_transaction() as s:
            org = await organization_id(s, args.org)
        if org is None:
            return _fail(f"unknown organization: {args.org}")
        async with db.tenant_transaction(org) as s:
            if args.outbound_command == "allow":
                target = outbound_targets.registration_target(args.url)
                found, added = await outbound_targets.add_target(
                    s, org, target=target, purpose=args.purpose, reason=reason or "", actor=_actor()
                )
                verb = "registered" if added else "already registered"
                print(f"{args.org}: {found.target.origin} {verb} for {found.purpose} ({found.id})")
            elif args.outbound_command == "revoke":
                revoked = await outbound_targets.revoke_target(
                    s, org, uuid.UUID(args.id), reason=reason or "", actor=_actor()
                )
                if revoked is None:
                    return _fail("no live registration with that ID in this organization")
                print(f"{args.org}: {revoked.target.origin} ({revoked.purpose}) revoked")
            else:
                for t in await outbound_targets.list_targets(s, org):
                    state = f"revoked {t.revoked_at:%Y-%m-%d}" if t.revoked_at else "live"
                    print(f"{t.id}  {t.purpose:10}  {t.target.origin}  {state}")
    except outbound_targets.TargetRejected as exc:
        return _fail(str(exc))
    except ValueError:
        return _fail("--id must be an ID")
    finally:
        await db.dispose()
    return 0


def add_create_parser(org_commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    create_cmd = org_commands.add_parser(
        "create", help="Create an organization, its OIDC connection and first administrator"
    )
    create_cmd.add_argument("--slug", required=True)
    create_cmd.add_argument("--name", required=True)
    create_cmd.add_argument("--issuer", required=True, help="exactly as in its discovery document")
    create_cmd.add_argument("--client-id", required=True)
    secret = create_cmd.add_mutually_exclusive_group()
    secret.add_argument("--client-secret-ref", help="env:LATTICE_OIDC_SECRET_<NAME>")
    secret.add_argument(
        "--client-secret-stdin",
        action="store_true",
        help="read the client secret from stdin and store it encrypted (never echoed)",
    )
    create_cmd.add_argument(
        "--subject-claim", default="sub", help="the ID-token claim that identifies a person"
    )
    create_cmd.add_argument(
        "--register-private-issuer",
        action="store_true",
        help="the issuer is the organization's own provider on a private network: register "
        "its exact origin as an outbound target (audited)",
    )
    create_cmd.add_argument("--first-admin-email", required=True)
    create_cmd.add_argument(
        "--first-admin-identifier",
        required=True,
        help="the exact value of the identifying claim for the first administrator",
    )
    create_cmd.add_argument("--first-admin-name")
    create_cmd.add_argument("--reason", required=True)


def add_parsers(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    outbound_cmd = commands.add_parser(
        "outbound", help="Private outbound targets registered per organization (C4)"
    )
    outbound_commands = outbound_cmd.add_subparsers(dest="outbound_command", required=True)
    allow = outbound_commands.add_parser("allow", help="Register an exact private target")
    allow.add_argument("--url", required=True, help="scheme://host[:port] (path ignored)")
    allow.add_argument("--purpose", required=True, choices=["oidc", "generation"])
    revoke = outbound_commands.add_parser("revoke", help="Revoke a registration")
    revoke.add_argument("--id", required=True)
    listing = outbound_commands.add_parser("list", help="Registrations, live and revoked")
    for command in (allow, revoke, listing):
        command.add_argument("--org", required=True)
    for command in (allow, revoke):
        command.add_argument("--reason", required=True)
