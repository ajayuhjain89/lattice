"""``lattice sso``, ``lattice scim token`` and ``lattice member admin`` (Phase 10): the
operator's identity controls, as ``lattice_owner`` through row-level security, each with
a reason and an audit event (``lattice.identity.admin``). Nothing here is reachable from
the API."""

import argparse
import os
import sys
import uuid

from lattice.config import load_migration_settings
from lattice.db.engine import Database, create_engine
from lattice.identity import admin
from lattice.ingestion.store import organization_id

COMMANDS = ("sso", "scim", "member")


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def _owner() -> Database:
    return Database(
        create_engine(
            load_migration_settings().migration_database_url.get_secret_value(),
            application_name="lattice-identity",
            pool_size=1,
        )
    )


def _origin() -> str:
    return os.environ.get("LATTICE_PUBLIC_URL", "http://127.0.0.1:3000").rstrip("/")


def _print_connection(org: str, found: admin.ConnectionView) -> None:
    origin = _origin()
    print(f"{org}: OIDC connection {found.id} ({found.status})")
    print(f"  issuer:          {found.issuer}")
    kind = "confidential" if found.confidential else "public, PKCE"
    print(f"  client ID:       {found.client_id} ({kind})")
    print(f"  identifying claim: {found.subject_claim} (must equal the SCIM externalId)")
    print(f"  sign-in URL:     {origin}/sso/{found.login_key}")
    print(f"  redirect URI:    {origin}/api/v1/auth/oidc/{found.login_key}/callback")
    print(f"  SCIM base URL:   {origin}/api/scim/v2")


async def run(args: argparse.Namespace) -> int:
    given = getattr(args, "reason", None)
    if given is not None and not given.strip():
        return _fail("--reason is required")
    reason = str(given or "")
    db = _owner()
    actor = f"operator:{os.environ.get('USER', 'cli')}"
    try:
        async with db.unscoped_transaction() as s:
            org = await organization_id(s, args.org)
        if org is None:
            return _fail(f"unknown organization: {args.org}")
        async with db.tenant_transaction(org) as s:
            if args.command == "sso" and args.sso_command == "show":
                found = await admin.active_connection(s, org)
                if found is None:
                    print(f"{args.org}: no active OIDC connection")
                else:
                    _print_connection(args.org, found)
            elif args.command == "sso" and args.sso_command == "add":
                found = await admin.add_connection(
                    s,
                    org,
                    issuer=args.issuer,
                    client_id=args.client_id,
                    secret_ref=args.client_secret_ref,
                    subject_claim=args.subject_claim,
                    reason=reason,
                    actor=actor,
                )
                _print_connection(args.org, found)
            elif args.command == "sso":
                found = await admin.disable_connection(s, org, reason=reason, actor=actor)
                print(
                    f"{args.org}: OIDC connection {found.id} disabled; its SCIM tokens stop working"
                )
            elif args.command == "scim" and args.scim_action == "create":
                token_id, token = await admin.create_scim_token(s, org, reason=reason, actor=actor)
                print(f"{args.org}: SCIM token {token_id} created. Shown once; store it in the")
                print("identity provider's provisioning settings now:")
                print(token)
                print(f"SCIM base URL: {_origin()}/api/scim/v2")
            elif args.command == "scim" and args.scim_action == "revoke":
                revoked = await admin.revoke_scim_token(
                    s, org, uuid.UUID(args.id), reason=reason, actor=actor
                )
                print(f"{args.org}: SCIM token {args.id} {'revoked' if revoked else 'not active'}")
            elif args.command == "scim":
                for row in await admin.scim_tokens(s, org):
                    state = (
                        f"revoked {row['revoked_at']:%Y-%m-%d}" if row["revoked_at"] else "active"
                    )
                    print(f"{row['id']}  created {row['created_at']:%Y-%m-%d}  {state}")
            else:
                changed = await admin.set_admin(
                    s, org, email=args.email, grant=args.grant, reason=reason, actor=actor
                )
                verb = "now" if changed else "already"
                print(
                    f"{args.org}: {args.email} is {verb} "
                    f"{'an administrator' if args.grant else 'not an administrator'} "
                    "(administration grants no access to content)"
                )
    except admin.OperatorRejected as exc:
        return _fail(str(exc))
    except ValueError:
        return _fail("--id must be a token ID")
    finally:
        await db.dispose()
    return 0


def add_parsers(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    sso = commands.add_parser("sso", help="The organization's OIDC connection (ADR-015)")
    sso_commands = sso.add_subparsers(dest="sso_command", required=True)
    add = sso_commands.add_parser("add", help="Connect an OIDC identity provider")
    add.add_argument("--issuer", required=True, help="Exactly as in its discovery document")
    add.add_argument("--client-id", required=True)
    add.add_argument(
        "--client-secret-ref",
        help="env:LATTICE_OIDC_SECRET_<NAME> for a confidential client (omit for PKCE only)",
    )
    add.add_argument(
        "--subject-claim",
        default="sub",
        help="The ID-token claim that identifies a person; the SCIM externalId must match it",
    )
    show = sso_commands.add_parser("show", help="The active connection and its URLs")
    disable = sso_commands.add_parser("disable", help="Disable the active connection")
    for command in (add, show, disable):
        command.add_argument("--org", required=True)
    for command in (add, disable):
        command.add_argument("--reason", required=True)

    scim = commands.add_parser("scim", help="SCIM provisioning tokens (D-20)")
    scim_commands = scim.add_subparsers(dest="scim_command", required=True)
    token = scim_commands.add_parser("token", help="Create, list or revoke SCIM tokens")
    scim_actions = token.add_subparsers(dest="scim_action", required=True)
    create = scim_actions.add_parser("create", help="Create a token (printed once)")
    revoke = scim_actions.add_parser("revoke", help="Revoke a token")
    revoke.add_argument("--id", required=True)
    listing = scim_actions.add_parser("list", help="List tokens (never their values)")
    for command in (create, revoke, listing):
        command.add_argument("--org", required=True)
    for command in (create, revoke):
        command.add_argument("--reason", required=True)

    member = commands.add_parser("member", help="Operator member controls")
    member_commands = member.add_subparsers(dest="member_command", required=True)
    admin_cmd = member_commands.add_parser(
        "admin", help="Give or take the administrator capability (no content access)"
    )
    admin_cmd.add_argument("--org", required=True)
    admin_cmd.add_argument("--email", required=True)
    choice = admin_cmd.add_mutually_exclusive_group(required=True)
    choice.add_argument("--grant", dest="grant", action="store_true")
    choice.add_argument("--revoke", dest="grant", action="store_false")
    admin_cmd.add_argument("--reason", required=True)
