"""``lattice org create``: the operator creates a tenant's trust root (D-28; C1).

One transaction creates the organization, its safe settings, the OIDC connection and
the first administrator's membership and exact directory identity -- or nothing. An
identical retry writes nothing; a differing one is refused and names the difference.
The end-to-end test signs the first administrator in through an in-process identity
provider with an encrypted client secret and reads the setup record as them.
"""

import asyncio
import io
import ipaddress
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import text

from lattice.cli import main
from lattice.db.engine import Database
from lattice.identity.oidc import OidcSignIn
from lattice.orgs import bootstrap
from lattice.orgs.bootstrap import BootstrapRejected, OrgRequest, create_organization
from lattice.outbound import IpAddress, OutboundRefused
from lattice.vault.keys import Keyring

from .conftest import CSRF, OWNER_URL, AppFactory
from .factories import create_member, create_oidc_connection, create_org
from .identity.fake_idp import FakeIdp, oidc_client

KEY = "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE="
KEYS = {"LATTICE_SECRET_KEYS": f"k1:{KEY}", "LATTICE_SECRET_ACTIVE_KEY": "k1"}
SECRET = "oidc-client-secret-value-0123456789"
ISSUER = "https://idp.halden.example"
ACTOR = "operator:test"


async def public(host: str, port: int) -> list[IpAddress]:
    return [ipaddress.ip_address("93.184.216.34")]


async def private(host: str, port: int) -> list[IpAddress]:
    return [ipaddress.ip_address("10.0.0.7")]


def request(**changes: Any) -> OrgRequest:
    values: dict[str, Any] = {
        "slug": "halden",
        "name": "Halden Logistics",
        "issuer": ISSUER,
        "client_id": "lattice",
        "subject_claim": "sub",
        "admin_email": "pat@halden.example",
        "admin_identifier": "sub-pat",
        "admin_name": "Pat Admin",
        "reason": "Customer onboarding #42",
    }
    values.update(changes)
    return OrgRequest(**values)


def keyring() -> Keyring:
    return Keyring.from_environment(KEYS)


async def create(owner_db: Database, req: OrgRequest, **kwargs: Any) -> bootstrap.OrgCreated:
    kwargs.setdefault("keyring", keyring())
    kwargs.setdefault("actor", ACTOR)
    kwargs.setdefault("resolver", public)
    return await create_organization(owner_db, req, **kwargs)


async def _rows(owner_db: Database, org: uuid.UUID, sql: str) -> list[Any]:
    async with owner_db.tenant_snapshot(org) as s:
        return list((await s.execute(text(sql), {"o": org})).mappings().all())


async def test_the_trust_root_is_created_in_one_transaction(owner_db: Database) -> None:
    created = await create(owner_db, request(client_secret=SecretStr(SECRET)))
    assert created.created
    org = created.organization_id
    [organization] = await _rows(owner_db, org, "SELECT * FROM organizations WHERE id = :o")
    assert (organization["slug"], organization["name"], organization["status"]) == (
        "halden",
        "Halden Logistics",
        "active",
    )
    [settings] = await _rows(owner_db, org, "SELECT * FROM org_settings")
    assert settings["external_egress_level"] == "internal"
    assert settings["organization_controlled_level"] is None
    assert settings["default_query_sensitivity"] == "internal"
    assert settings["data_origin"] == "customer"
    [connection] = await _rows(owner_db, org, "SELECT * FROM oidc_connections")
    assert connection["issuer"] == ISSUER
    assert connection["status"] == "active"
    assert connection["client_secret_ref"].startswith("vault:")
    assert connection["last_success_at"] is None  # not verified until the first sign-in
    [member] = await _rows(
        owner_db,
        org,
        "SELECT m.*, u.email, u.display_name, u.home_organization_id FROM memberships m "
        "JOIN users u ON u.id = m.user_id",
    )
    assert member["is_admin"] is True
    assert (member["status"], member["clearance"], member["account_scope"]) == (
        "active",
        "internal",
        "assigned",
    )
    assert member["role_label"] is None
    assert member["home_organization_id"] == org
    assert (member["email"], member["display_name"]) == ("pat@halden.example", "Pat Admin")
    for table in ("account_assignments", "membership_departments", "access_group_members"):
        assert await _rows(owner_db, org, f"SELECT 1 FROM {table}") == []  # noqa: S608
    [identity] = await _rows(owner_db, org, "SELECT * FROM directory_identities")
    assert identity["external_id"] == "sub-pat"
    assert identity["connection_id"] == connection["id"]
    assert identity["membership_id"] == member["id"]
    [secret] = await _rows(owner_db, org, "SELECT * FROM encrypted_secrets")
    assert secret["owner_kind"] == "oidc_client_secret"
    assert secret["owner_id"] == connection["id"]
    assert SECRET.encode() not in bytes(secret["ciphertext"])
    events = await _rows(
        owner_db, org, "SELECT actor, action, target, before, after, reason FROM audit_events"
    )
    assert sorted(e["action"] for e in events) == [
        "access.member_changed",
        "organization.created",
        "sso.connection_created",
    ]
    assert {e["actor"] for e in events} == {ACTOR}
    assert {e["reason"] for e in events} == {"Customer onboarding #42"}
    by_action = {e["action"]: e for e in events}
    assert by_action["sso.connection_created"]["after"]["client_secret"] == "encrypted"
    assert by_action["access.member_changed"]["after"]["is_admin"] is True
    assert by_action["organization.created"]["after"]["data_origin"] == "customer"
    assert SECRET not in str(events)


async def test_an_identical_retry_writes_nothing(owner_db: Database) -> None:
    first = await create(owner_db, request(client_secret=SecretStr(SECRET)))
    again = await create(owner_db, request(client_secret=SecretStr(f"{SECRET}\n")))
    assert again.created is False
    assert (again.organization_id, again.login_key) == (first.organization_id, first.login_key)
    events = await _rows(owner_db, first.organization_id, "SELECT 1 FROM audit_events")
    assert len(events) == 3
    by_reference = await create(
        owner_db,
        request(slug="veridian", client_id="v", client_secret_ref="env:LATTICE_OIDC_SECRET_V"),
    )
    assert (
        await create(
            owner_db,
            request(slug="veridian", client_id="v", client_secret_ref="env:LATTICE_OIDC_SECRET_V"),
        )
    ).created is False
    assert by_reference.client_secret == "reference"


@pytest.mark.parametrize(
    ("changes", "named"),
    [
        ({"name": "Halden Group"}, "name"),
        ({"issuer": "https://login.halden.example"}, "issuer"),
        ({"client_id": "other"}, "client ID"),
        ({"subject_claim": "email"}, "identifying claim"),
        ({"client_secret": SecretStr("another-secret-value")}, "client secret"),
        (
            {"client_secret": None, "client_secret_ref": "env:LATTICE_OIDC_SECRET_X"},
            "client secret",
        ),
        ({"admin_identifier": "sub-someone-else"}, "first administrator identifier"),
        ({"admin_email": "kim@halden.example"}, "first administrator e-mail"),
        ({"admin_name": "Pat Other"}, "first administrator name"),
    ],
)
async def test_a_differing_retry_is_refused_and_changes_nothing(
    owner_db: Database, changes: dict[str, Any], named: str
) -> None:
    first = await create(owner_db, request(client_secret=SecretStr(SECRET)))
    before = await _rows(owner_db, first.organization_id, "SELECT count(*) AS n FROM audit_events")
    with pytest.raises(BootstrapRejected, match=f"already exists and differs: .*{named}"):
        await create(owner_db, request(**{"client_secret": SecretStr(SECRET), **changes}))
    after = await _rows(owner_db, first.organization_id, "SELECT count(*) AS n FROM audit_events")
    assert before == after
    [organization] = await _rows(
        owner_db, first.organization_id, "SELECT name FROM organizations WHERE id = :o"
    )
    assert organization["name"] == "Halden Logistics"


async def test_slugs_are_exact_and_case_insensitive_for_conflicts(owner_db: Database) -> None:
    await create(owner_db, request())
    for slug in ("Halden", "halden_x", "-halden", ""):
        with pytest.raises(BootstrapRejected, match="--slug"):
            await create(owner_db, request(slug=slug))
    # An existing organization of another origin (seeded, say) is never adopted.
    await create_org(owner_db, "veridian")
    with pytest.raises(BootstrapRejected, match="already exists and differs"):
        await create(owner_db, request(slug="veridian", client_id="v"))


async def test_the_identifier_is_stored_exactly(owner_db: Database) -> None:
    # NFC-normalised and trimmed, case preserved -- an identifier, never an e-mail match.
    decomposed = "  Pat.Ölund@Halden.example "
    created = await create(
        owner_db,
        request(
            subject_claim="email",
            admin_identifier=decomposed.replace("Ö", "Ö"),
            admin_email="pat@halden.example",
        ),
    )
    [identity] = await _rows(
        owner_db, created.organization_id, "SELECT external_id FROM directory_identities"
    )
    assert identity["external_id"] == "Pat.Ölund@Halden.example"


async def test_nothing_is_linked_by_e_mail(owner_db: Database) -> None:
    other = await create_org(owner_db, "veridian")
    existing = await create_member(owner_db, other, "pat@halden.example")
    created = await create(owner_db, request())
    [member] = await _rows(
        owner_db,
        created.organization_id,
        "SELECT m.user_id, u.home_organization_id FROM memberships m JOIN users u "
        "ON u.id = m.user_id",
    )
    assert member["user_id"] != existing.user_id  # a new user of this organization
    assert member["home_organization_id"] == created.organization_id
    # The other organization is untouched.
    rows = await _rows(owner_db, other, "SELECT 1 FROM memberships")
    assert len(rows) == 1


async def test_an_identity_provider_client_of_another_organization_is_refused(
    owner_db: Database,
) -> None:
    other = await create_org(owner_db, "veridian")
    await create_oidc_connection(owner_db, other, issuer=ISSUER, client_id="lattice")
    with pytest.raises(BootstrapRejected, match="registered to another organization"):
        await create(owner_db, request())
    async with owner_db.unscoped_transaction() as s:
        assert (
            await s.execute(text("SELECT count(*) FROM organizations WHERE slug = 'halden'"))
        ).scalar_one() == 0


async def test_a_failure_rolls_everything_back(
    owner_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = bootstrap.access_snapshot  # type: ignore[attr-defined]

    async def broken(*args: Any, **kwargs: Any) -> Any:
        await real(*args, **kwargs)
        raise RuntimeError("induced failure after every insert")

    monkeypatch.setattr(bootstrap, "access_snapshot", broken)
    with pytest.raises(RuntimeError, match="induced"):
        await create(owner_db, request(client_secret=SecretStr(SECRET)))
    async with owner_db.unscoped_transaction() as s:
        assert (await s.execute(text("SELECT count(*) FROM organizations"))).scalar_one() == 0
        assert (await s.execute(text("SELECT count(*) FROM users"))).scalar_one() == 0
    monkeypatch.setattr(bootstrap, "access_snapshot", real)
    assert (await create(owner_db, request(client_secret=SecretStr(SECRET)))).created


async def test_the_issuer_must_be_public_or_registered(owner_db: Database) -> None:
    with pytest.raises(BootstrapRejected, match="not public"):
        await create(owner_db, request(), resolver=private)
    with pytest.raises(BootstrapRejected, match="https://"):
        await create(owner_db, request(issuer="http://idp.halden.example"))

    async def unreachable(host: str, port: int) -> list[IpAddress]:
        raise OutboundRefused("target_unreachable")

    with pytest.raises(BootstrapRejected, match="target_unreachable"):
        await create(owner_db, request(), resolver=unreachable)
    created = await create(
        owner_db,
        request(issuer="https://idp.halden.internal:9443", register_private_issuer=True),
        resolver=private,
    )
    [target] = await _rows(owner_db, created.organization_id, "SELECT * FROM outbound_targets")
    assert (target["purpose"], target["scheme"], target["host"], target["port"]) == (
        "oidc",
        "https",
        "idp.halden.internal",
        9443,
    )
    events = await _rows(
        owner_db, created.organization_id, "SELECT action FROM audit_events ORDER BY created_at"
    )
    assert "outbound.target_added" in {e["action"] for e in events}

    async def metadata(host: str, port: int) -> list[IpAddress]:
        return [ipaddress.ip_address("169.254.169.254")]

    with pytest.raises(BootstrapRejected, match="not public"):
        await create(
            owner_db,
            request(slug="evil", client_id="e", register_private_issuer=True),
            resolver=metadata,
        )


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        (
            {"client_secret": SecretStr(SECRET), "client_secret_ref": "env:LATTICE_OIDC_SECRET_X"},
            "not both",
        ),
        ({"client_secret_ref": "env:LATTICE_DATABASE_URL"}, "LATTICE_OIDC_SECRET_"),
        ({"admin_email": "not-an-email"}, "e-mail"),
        ({"admin_identifier": "  "}, "identifier"),
        ({"admin_identifier": "a\nb"}, "identifier"),
        ({"name": "x" * 201}, "--name"),
        ({"subject_claim": "sub claim"}, "claim"),
        ({"reason": "  "}, "reason"),
    ],
)
async def test_invalid_input_is_refused_before_anything_is_written(
    owner_db: Database, changes: dict[str, Any], fragment: str
) -> None:
    with pytest.raises(BootstrapRejected, match=fragment) as refused:
        await create(owner_db, request(**changes))
    assert SECRET not in str(refused.value)


async def test_a_secret_from_stdin_needs_the_keys(owner_db: Database) -> None:
    with pytest.raises(BootstrapRejected, match="LATTICE_SECRET_KEYS"):
        await create(owner_db, request(client_secret=SecretStr(SECRET)), keyring=Keyring())


@pytest.fixture
def cli_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LATTICE_MIGRATION_DATABASE_URL", OWNER_URL)
    monkeypatch.setenv("LATTICE_PUBLIC_URL", "https://lattice.example.com")
    for name, value in KEYS.items():
        monkeypatch.setenv(name, value)


async def test_the_cli_prints_no_secret(
    owner_db: Database,
    cli_environment: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(SECRET + "\n"))
    argv = [
        "org",
        "create",
        "--slug",
        "halden",
        "--name",
        "Halden Logistics",
        "--issuer",
        "https://localhost:9443",
        "--register-private-issuer",
        "--client-id",
        "lattice",
        "--client-secret-stdin",
        "--first-admin-email",
        "pat@halden.example",
        "--first-admin-identifier",
        "sub-pat",
        "--reason",
        "Onboarding",
    ]
    assert await asyncio.to_thread(main, argv) == 0
    out, err = capsys.readouterr()
    assert "organization created" in out
    assert "https://lattice.example.com/sso/" in out
    assert "https://lattice.example.com/api/v1/auth/oidc/" in out
    assert "secret encrypted at rest" in out
    assert SECRET not in out + err
    assert KEY not in out + err
    monkeypatch.setattr("sys.stdin", io.StringIO(SECRET))
    assert await asyncio.to_thread(main, argv) == 0
    assert "already exists, unchanged" in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", io.StringIO("a-different-secret"))
    assert await asyncio.to_thread(main, argv) == 2
    out, err = capsys.readouterr()
    assert "differs: client secret" in err
    assert "a-different-secret" not in out + err
    monkeypatch.setattr("sys.stdin", io.StringIO("   \n"))
    assert await asyncio.to_thread(main, argv) == 2
    assert "empty" in capsys.readouterr().err


async def test_the_first_administrator_signs_in_and_sees_the_setup_record(
    owner_db: Database,
    app_client: AppFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bootstrap end to end: the operator creates the organization with an
    encrypted client secret; the first administrator signs in through the identity
    provider (the API decrypts the secret for the token exchange); the sign-in verifies
    the connection, and the setup record says so."""
    created = await create(owner_db, request(client_secret=SecretStr(SECRET)))
    idp = FakeIdp(issuer=ISSUER, client_id="lattice", client_secret=SECRET)
    for name, value in KEYS.items():
        monkeypatch.setenv(name, value)

    def configure(app: FastAPI) -> None:
        app.state.oidc = OidcSignIn(
            app.state.db,
            oidc_client(idp.handler),
            web_origin="http://127.0.0.1:3000",
            secrets=app.state.ask_dependencies.secrets,
        )

    async with app_client(configure) as client:
        start = await client.get(f"/v1/auth/oidc/{created.login_key}/start")
        assert start.status_code == 303
        callback = idp.authorize(start.headers["location"], "sub-pat")
        path = callback.split("127.0.0.1:3000", 1)[1].removeprefix("/api")
        done = await client.get(path)
        assert done.status_code == 303
        assert done.headers["location"] == "/ask"
        me = (await client.get("/v1/me")).json()
        assert me["user"]["email"] == "pat@halden.example"
        setup = await client.get("/v1/admin/setup")
        assert setup.status_code == 200
        categories = {c["key"]: c for c in setup.json()["categories"]}
        assert (categories["identity"]["state"], categories["identity"]["reason"]) == (
            "complete",
            "verified",
        )
        assert categories["organization"]["reason"] == "single_administrator"
        assert categories["members"]["reason"] == "only_administrators"
        assert SECRET not in setup.text
        # The token exchange presented the decrypted secret (basic authentication).
        token_calls = [r for r in idp.requests if r.url.path.endswith("/token")]
        assert token_calls
        assert token_calls[-1].headers["authorization"].startswith("Basic ")
        rename = await client.patch(
            "/v1/admin/organization",
            json={
                "name": "Halden Group",
                "version": setup.json()["organization"]["version"],
                "reason": "Rebrand",
            },
            headers=CSRF,
        )
        assert rename.status_code == 200
        assert rename.json()["name"] == "Halden Group"
