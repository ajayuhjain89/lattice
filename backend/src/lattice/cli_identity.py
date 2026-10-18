"""The operator's identity and incident controls (Phase 10; Phase 11 P11.3, D-25, D-26),
as ``lattice_owner`` through row-level security, each with a reason and an audit event
(``lattice.identity.admin``). Nothing here is reachable from the API.

    lattice sso add|show|list|disable|enable|update --org SLUG ...
    lattice scim token create|list|revoke --org SLUG ...
    lattice member admin|suspend|reinstate --org SLUG --email E --reason R
    lattice session revoke --org SLUG (--email E | --all) --reason R
    lattice org suspend|reactivate --org SLUG --reason R
"""

import argparse
import os
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from lattice.cli_secrets import operator_keyring, read_secret_stdin
from lattice.config import ConfigurationError, load_migration_settings
from lattice.db.engine import Database, create_engine
from lattice.identity import admin
from lattice.ingestion.store import organization_id
from lattice.orgs.outbound_targets import access_class, policy_for
from lattice.outbound import OutboundRefused, parse_target

COMMANDS = ("sso", "scim", "member", "session")


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


def _id(value: str | None) -> uuid.UUID | None:
    return None if value is None else uuid.UUID(value)


def _origin() -> str:
    return os.environ.get("LATTICE_PUBLIC_URL", "http://127.0.0.1:3000").rstrip("/")


async def _print_outbound(s: AsyncSession, org_id: uuid.UUID, org: str, issuer: str) -> None:
    """How the outbound policy classes the issuer (P12.4, C4): sign-in reaches a private
    or non-standard address only as a target registered for the organization."""
    access = access_class(await policy_for(s, org_id, "oidc"), issuer)
    if access == "not_permitted":
        try:
            origin = parse_target(issuer).origin
        except OutboundRefused:
            origin = issuer
        print(
            "  outbound policy: NOT PERMITTED -- sign-in is refused until it is registered: "
            f"lattice outbound allow --org {org} --url {origin} --purpose oidc --reason ..."
        )
    else:
        shown = {"public": "public (checked at each connection)"}.get(
            access, "operator-approved private target"
        )
        print(f"  outbound policy: {shown}")


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
                    await _print_outbound(s, org, args.org, found.issuer)
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
                    new_identity_namespace=args.new_identity_namespace,
                )
                _print_connection(args.org, found)
                await _print_outbound(s, org, args.org, found.issuer)
            elif args.command == "sso" and args.sso_command == "list":
                for c in await admin.connections(s, org):
                    print(f"{c.id}  {c.status:8}  {c.issuer}  client {c.client_id}")
            elif args.command == "sso" and args.sso_command == "disable":
                disabled, revoked = await admin.disable_connection(
                    s, org, reason=reason, actor=actor
                )
                print(
                    f"{args.org}: OIDC connection {disabled.id} disabled; {revoked} session(s) "
                    "it issued revoked; its SCIM tokens stop working"
                )
            elif args.command == "sso" and args.sso_command == "enable":
                found = await admin.enable_connection(
                    s, org, connection_id=_id(args.connection_id), reason=reason, actor=actor
                )
                _print_connection(args.org, found)
            elif args.command == "sso":
                secret: object = admin._UNSET
                value = read_secret_stdin() if args.client_secret_stdin else None
                if args.no_client_secret:
                    secret = None
                elif args.client_secret_ref is not None:
                    secret = args.client_secret_ref
                updated, revoked = await admin.update_connection(
                    s,
                    org,
                    connection_id=_id(args.connection_id),
                    client_id=args.client_id,
                    secret_ref=secret,
                    issuer=args.issuer,
                    subject_claim=args.subject_claim,
                    subjects_unchanged=args.subjects_unchanged,
                    secret_value=value,
                    keyring=operator_keyring() if value is not None else None,
                    **(
                        {"step_up_acr": None}
                        if args.no_step_up_acr
                        else {"step_up_acr": args.step_up_acr}
                        if args.step_up_acr
                        else {}
                    ),
                    reason=reason,
                    actor=actor,
                )
                _print_connection(args.org, updated)
                await _print_outbound(s, org, args.org, updated.issuer)
                if revoked:
                    print(f"  {revoked} session(s) of this connection revoked")
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
            elif args.command == "member" and args.member_command == "suspend":
                suspended = await admin.suspend_member(
                    s, org, email=args.email, reason=reason, actor=actor
                )
                if suspended is None:
                    print(f"{args.org}: {args.email} is already suspended")
                else:
                    member, revoked = suspended
                    print(f"{args.org}: {args.email} suspended; {revoked} session(s) revoked")
                    if member.provisioned:
                        print(
                            "  provisioned by the identity provider: suspend them there too, "
                            "or its next update may reactivate them (D-19)"
                        )
            elif args.command == "member" and args.member_command == "reinstate":
                reinstated = await admin.reinstate_member(
                    s, org, email=args.email, reason=reason, actor=actor
                )
                print(
                    f"{args.org}: {args.email} is "
                    f"{'already active' if reinstated is None else 'active again'}"
                )
            elif args.command == "session":
                revoked = await admin.revoke_sessions(
                    s,
                    org,
                    email=None if args.all_sessions else args.email,
                    reason=reason,
                    actor=actor,
                )
                whose = args.email or "every member"
                print(f"{args.org}: {revoked} session(s) of {whose} revoked")
            elif args.command == "org":
                result = await admin.set_organization_status(
                    s, org, suspend=args.org_command == "suspend", reason=reason, actor=actor
                )
                if result is None:
                    state = "suspended" if args.org_command == "suspend" else "active"
                    print(f"{args.org}: already {state}")
                elif args.org_command == "suspend":
                    print(
                        f"{args.org}: suspended; sign-in, SCIM and requested syncs stop; "
                        f"{result} session(s) revoked"
                    )
                else:
                    print(f"{args.org}: reactivated (members sign in again)")
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
    except ConfigurationError as exc:
        return _fail(str(exc))
    except ValueError as exc:
        if getattr(args, "client_secret_stdin", False):
            return _fail(str(exc))  # the secret is empty or too long (never its value)
        return _fail("--id / --connection-id must be an ID")
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
    add.add_argument(
        "--new-identity-namespace",
        action="store_true",
        help="Add even though a disabled connection has provisioned members (they will not map)",
    )
    show = sso_commands.add_parser("show", help="The active connection and its URLs")
    listing_sso = sso_commands.add_parser("list", help="Every connection, active and disabled")
    disable = sso_commands.add_parser(
        "disable", help="Disable the active connection and revoke the sessions it issued"
    )
    enable = sso_commands.add_parser(
        "enable", help="Re-activate a disabled connection (same members, same login key)"
    )
    update = sso_commands.add_parser("update", help="Change a connection in place")
    update.add_argument("--client-id")
    secret = update.add_mutually_exclusive_group()
    secret.add_argument("--client-secret-ref", help="env:LATTICE_OIDC_SECRET_<NAME> (rotation)")
    secret.add_argument("--no-client-secret", action="store_true", help="a public PKCE client")
    secret.add_argument(
        "--client-secret-stdin",
        action="store_true",
        help="read the new client secret from stdin and store it encrypted (never echoed)",
    )
    update.add_argument("--issuer")
    update.add_argument("--subject-claim")
    acr = update.add_mutually_exclusive_group()
    acr.add_argument(
        "--step-up-acr",
        action="append",
        metavar="ACR",
        help="an acr value an administrator's egress step-up must assert (repeatable; "
        "D-34): only what your identity provider really asserts for a strong method",
    )
    acr.add_argument(
        "--no-step-up-acr",
        action="store_true",
        help="no assurance requirement (a fresh sign-in is still required)",
    )
    update.add_argument(
        "--subjects-unchanged",
        action="store_true",
        help="Required with --issuer/--subject-claim: every person keeps the same subject",
    )
    for command in (enable, update):
        command.add_argument("--connection-id", help="needed when several could match")
    for command in (add, show, listing_sso, disable, enable, update):
        command.add_argument("--org", required=True)
    for command in (add, disable, enable, update):
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
    for name, help_text in (
        ("suspend", "Suspend a member now and revoke their sessions (audited)"),
        ("reinstate", "Make a suspended or removed member active again (audited)"),
    ):
        lifecycle = member_commands.add_parser(name, help=help_text)
        lifecycle.add_argument("--org", required=True)
        lifecycle.add_argument("--email", required=True)
        lifecycle.add_argument("--reason", required=True)

    session_cmd = commands.add_parser("session", help="End sessions (audited)")
    session_commands = session_cmd.add_subparsers(dest="session_command", required=True)
    revoke_sessions = session_commands.add_parser("revoke", help="Revoke sessions now")
    revoke_sessions.add_argument("--org", required=True)
    whose = revoke_sessions.add_mutually_exclusive_group(required=True)
    whose.add_argument("--email", help="one member's sessions")
    whose.add_argument(
        "--all", dest="all_sessions", action="store_true", help="every session of the organization"
    )
    revoke_sessions.add_argument("--reason", required=True)
