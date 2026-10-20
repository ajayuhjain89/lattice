"""``lattice models ...`` and ``lattice provider approve`` (Phase 5 closure).

* ``models list`` -- the pinned local models, their license, size and cache state.
* ``models fetch NAME`` -- the only command that downloads model weights: the pinned
  files, verified by size and SHA-256, into ``LATTICE_MODEL_DIR``. Never implicit.
* ``models verify NAME`` -- re-hash the cached files.
* ``provider approve`` -- set a recorded provider's ``approved_for_level`` for one
  organization and purpose: the explicit administrator action of security-model §6,
  recorded as ``approved_at`` (an ``organization_controlled`` provider needs it, D-15).
  It cannot create a record or change its trust class, and does not touch the
  organization's ceilings.
* ``provider configure|enable|disable`` -- the organization's provider configuration
  (Phase 6, D-16): a new record is approved for ``internal`` only; for generation it
  becomes the one enabled configuration. The recorded trust class never changes.
* ``provider credential set|revoke`` -- organization-owned credentials by reference
  (``env:LATTICE_PROVIDER_SECRET_<NAME>``); the value is never read here. ``set`` on an
  existing credential rotates it (the old reference is revoked).
* ``provider list`` -- the masked configuration (never a secret or its reference).
* ``org egress`` -- the organization's ceilings for ``external_api`` and
  ``organization_controlled`` inference.

Every change needs ``--reason`` and writes an append-only ``audit_events`` row.
"""

import argparse
import logging
import os
import sys

from lattice import cli_org
from lattice.cli_secrets import operator_keyring, read_secret_stdin
from lattice.config import ConfigurationError, load_migration_settings, load_provider_settings
from lattice.db.engine import Database, create_engine
from lattice.ingestion.store import organization_id
from lattice.log import log_event
from lattice.providers import configuration
from lattice.providers.egress import DEPLOYMENTS
from lattice.providers.local_models import (
    MODELS,
    LocalModelUnavailable,
    check_present,
    fetch,
    spec_for,
    verify,
)
from lattice.providers.secrets import EnvironmentSecretStore

_log = logging.getLogger("lattice.providers")
LEVELS = ("internal", "confidential", "restricted")
PURPOSES = ("embedding", "generation")


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


def _owner() -> Database:
    return Database(
        create_engine(
            load_migration_settings().migration_database_url.get_secret_value(),
            application_name="lattice-provider",
            pool_size=1,
        )
    )


_CLASS_NOTE = {
    "local": "",
    "external_api": " (still capped by the organization's external egress level)",
    "organization_controlled": (
        " (still capped by the organization's organization_controlled level; "
        "nothing is sent while that is unset)"
    ),
}


async def provider(args: argparse.Namespace) -> int:
    """``lattice provider approve|configure|enable|disable|credential|list`` and
    ``lattice org egress``: administrative actions, each with a reason and an audit
    event (as lattice_owner, through row-level security)."""
    reason = getattr(args, "reason", None)
    if reason is not None and not reason.strip():
        return _fail("--reason is required")
    try:
        keyring = operator_keyring()
    except ConfigurationError as exc:
        return _fail(str(exc))
    value = None
    if getattr(args, "value_stdin", False):
        try:
            value = read_secret_stdin()
        except ValueError as exc:
            return _fail(str(exc))
    db = _owner()
    try:
        async with db.unscoped_transaction() as s:
            org = await organization_id(s, args.org)
        if org is None:
            return _fail(f"unknown organization: {args.org}")
        command = args.provider_command if args.command == "provider" else "egress"
        async with db.tenant_transaction(org) as s:
            if command == "list":
                views = await configuration.provider_overview(
                    s, org, EnvironmentSecretStore(keyring=keyring)
                )
                for v in views:
                    c = v.credential
                    credential = (
                        "no credential"
                        if c.state == "none"
                        else f"credential {c.source} "
                        f"({'resolvable' if c.resolvable else 'NOT resolvable'})"
                    )
                    print(
                        f"{v.purpose:<10} {v.provider}/{v.model} {v.deployment} "
                        f"approved {v.approved_for_level}"
                        f"{' (explicit)' if v.explicitly_approved else ''} "
                        f"{'enabled' if v.enabled else 'DISABLED'}; {credential}"
                    )
                return 0
            actor = f"operator:{os.environ.get('USER', 'cli')}"
            if command == "approve":
                row = await configuration.approve(
                    s,
                    org,
                    purpose=args.purpose,
                    provider=args.provider,
                    model=args.model,
                    level=args.level,
                    reason=args.reason,
                    actor=actor,
                )
                note = _CLASS_NOTE.get(row["deployment"], " (unknown class: nothing is sent)")
                print(f"{args.org}: {args.provider}/{args.model} approved for {args.level}{note}")
                if not row["training_opt_out"]:
                    print(
                        "  note: this provider may use submitted content; the egress policy "
                        "sends it content from synthetic organizations only"
                    )
                log_event(
                    _log,
                    logging.WARNING,
                    "provider.approval_changed",
                    organization_id=str(org),
                    purpose=args.purpose,
                    provider=args.provider,
                    model=args.model,
                    deployment=row["deployment"],
                    approved_for_level=args.level,
                )
            elif command == "configure":
                await configuration.configure_provider(
                    s,
                    org,
                    purpose=args.purpose,
                    provider=args.provider,
                    model=args.model,
                    deployment=args.deployment,
                    endpoint=args.endpoint,
                    retention_mode=args.retention_mode,
                    training_opt_out=not args.may_use_content,
                    reason=args.reason,
                    actor=actor,
                )
                print(
                    f"{args.org}: {args.purpose} provider {args.provider}/{args.model} "
                    f"({args.deployment}) configured and enabled, approved for internal only"
                )
            elif command in ("enable", "disable"):
                await configuration.set_enabled(
                    s,
                    org,
                    purpose=args.purpose,
                    provider=args.provider,
                    model=args.model,
                    enabled=command == "enable",
                    reason=args.reason,
                    actor=actor,
                )
                print(f"{args.org}: {args.provider}/{args.model} {command}d")
            elif command == "credential" and args.credential_command == "set":
                await configuration.set_credential(
                    s,
                    org,
                    provider=args.provider,
                    secret_ref=args.secret_ref,
                    value=value,
                    keyring=keyring,
                    data_use=args.data_use,
                    reason=args.reason,
                    actor=actor,
                )
                if value is None:
                    print(
                        f"{args.org}: {args.provider} credential reference recorded "
                        "(value not read)"
                    )
                else:
                    print(f"{args.org}: {args.provider} credential stored encrypted (not shown)")
            elif command == "credential":
                revoked = await configuration.revoke_credential(
                    s, org, provider=args.provider, reason=args.reason, actor=actor
                )
                print(
                    f"{args.org}: {args.provider} credential "
                    f"{'revoked' if revoked else 'none active'}"
                )
            else:
                after = await configuration.set_egress_levels(
                    s,
                    org,
                    external_level=args.external_level,
                    organization_controlled_level=(
                        "unset"
                        if args.organization_controlled_level is None
                        else None
                        if args.organization_controlled_level == "none"
                        else args.organization_controlled_level
                    ),
                    reason=args.reason,
                    actor=actor,
                )
                print(f"{args.org}: egress levels now {after}")
    except configuration.ConfigurationRejected as exc:
        return _fail(str(exc))
    finally:
        await db.dispose()
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

    provider_cmd = commands.add_parser(
        "provider", help="Organization provider configuration (administrative, audited)"
    )
    provider_commands = provider_cmd.add_subparsers(dest="provider_command", required=True)

    def named(sub: argparse.ArgumentParser, *, model: bool = True) -> None:
        sub.add_argument("--org", required=True)
        sub.add_argument("--purpose", choices=PURPOSES, default="embedding")
        sub.add_argument("--provider", required=True)
        if model:
            sub.add_argument("--model", required=True)
        sub.add_argument("--reason", required=True)

    approve_cmd = provider_commands.add_parser(
        "approve", help="Set a recorded provider's approved level (explicit approval)"
    )
    named(approve_cmd)
    approve_cmd.add_argument("--level", choices=LEVELS, required=True)
    configure_cmd = provider_commands.add_parser(
        "configure", help="Record a provider (approved for internal only; generation: enabled)"
    )
    named(configure_cmd)
    configure_cmd.add_argument("--deployment", choices=DEPLOYMENTS, required=True)
    configure_cmd.add_argument("--endpoint", help="local or organization-controlled server URL")
    configure_cmd.add_argument("--retention-mode", default="none")
    configure_cmd.add_argument(
        "--may-use-content",
        action="store_true",
        help="the provider's terms let it use submitted content (synthetic data only)",
    )
    for name in ("enable", "disable"):
        named(provider_commands.add_parser(name, help=f"{name.title()} a provider record"))
    credential_cmd = provider_commands.add_parser(
        "credential", help="Organization-owned credentials (references or encrypted values)"
    )
    credential_commands = credential_cmd.add_subparsers(dest="credential_command", required=True)
    set_cmd = credential_commands.add_parser(
        "set",
        help="Record (or rotate to) a credential: a reference (env:LATTICE_PROVIDER_SECRET_X) "
        "or a value read from stdin and encrypted at rest",
    )
    set_cmd.add_argument("--org", required=True)
    set_cmd.add_argument("--provider", required=True)
    source = set_cmd.add_mutually_exclusive_group(required=True)
    source.add_argument("--secret-ref", help="env:LATTICE_PROVIDER_SECRET_<NAME>")
    source.add_argument(
        "--value-stdin",
        action="store_true",
        help="read the key from standard input and store it encrypted (vault:); never echoed",
    )
    set_cmd.add_argument("--data-use", choices=["unpaid", "paid"])
    set_cmd.add_argument("--reason", required=True)
    revoke_cmd = credential_commands.add_parser("revoke", help="Revoke the active credential")
    revoke_cmd.add_argument("--org", required=True)
    revoke_cmd.add_argument("--provider", required=True)
    revoke_cmd.add_argument("--reason", required=True)
    list_cmd = provider_commands.add_parser("list", help="Masked provider configuration")
    list_cmd.add_argument("--org", required=True)

    org_cmd = commands.add_parser("org", help="Organization settings (administrative, audited)")
    org_commands = org_cmd.add_subparsers(dest="org_command", required=True)
    cli_org.add_create_parser(org_commands)
    egress_cmd = org_commands.add_parser("egress", help="The organization's AI egress ceilings")
    egress_cmd.add_argument("--org", required=True)
    egress_cmd.add_argument(
        "--external-level",
        choices=(*LEVELS, "none"),
        help="'none': external APIs are not allowed at all",
    )
    egress_cmd.add_argument(
        "--organization-controlled-level",
        choices=[*LEVELS, "none"],
        help="'none': the organization_controlled class is not allowed",
    )
    egress_cmd.add_argument("--reason", required=True)
    # Emergency stop (P11.3, D-26): handled by lattice.cli_identity.
    for name, help_text in (
        ("suspend", "Stop the organization: sign-in, SCIM, worker; revoke every session"),
        ("reactivate", "Reactivate a suspended organization"),
    ):
        lifecycle = org_commands.add_parser(name, help=help_text)
        lifecycle.add_argument("--org", required=True)
        lifecycle.add_argument("--reason", required=True)
