"""Operator and process boundaries of recoverable credentials (C3.3, C3.4):

* ``lattice secrets rekey`` re-encrypts, audits per organization with counts only, is
  repeatable, and refuses unknown key IDs before writing;
* ``sso update --client-secret-stdin`` replaces an OIDC client secret (old ciphertext
  destroyed), never echoing it;
* sign-in facts for the setup record: success verifies, provider failures are recorded,
  a person cancelling at the provider is not;
* only the API holds keys: a production worker or migration job with keys is refused; a
  production API refuses to start when stored ciphertext needs a key it does not hold.
"""

import asyncio
import io
import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import text

from lattice.cli import main
from lattice.config import ConfigurationError
from lattice.db.engine import Database
from lattice.identity.oidc import OidcClient, OidcSignIn
from lattice.vault.keys import Keyring
from lattice.vault.records import store_secret
from lattice.vault.startup import SecretKeysMissing, verify_secret_keys

from .conftest import OWNER_URL, AppFactory
from .factories import create_oidc_connection, create_org, create_provisioned_member
from .identity.fake_idp import FakeIdp

KEY_A = "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE="
KEY_B = "QkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkI="
SECRET = "client-secret-for-rotation-0001"


def ring(active: str = "a", **keys: str) -> Keyring:
    listed = keys or {"a": KEY_A}
    return Keyring.from_environment(
        {
            "LATTICE_SECRET_KEYS": ",".join(f"{k}:{v}" for k, v in listed.items()),
            "LATTICE_SECRET_ACTIVE_KEY": active,
        }
    )


async def _store(owner_db: Database, org: uuid.UUID, keyring: Keyring) -> str:
    async with owner_db.tenant_transaction(org) as s:
        return await store_secret(
            s,
            keyring,
            organization_id=org,
            owner_kind="provider_api_key",
            owner_id=uuid.uuid4(),
            value=SecretStr("sk-value"),
        )


async def test_the_rekey_command(
    owner_db: Database, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    halden = await create_org(owner_db, "halden")
    await create_org(owner_db, "veridian")  # nothing stored: no event
    await _store(owner_db, halden, ring("a"))
    await _store(owner_db, halden, ring("a"))
    monkeypatch.setenv("LATTICE_MIGRATION_DATABASE_URL", OWNER_URL)
    command = ["secrets", "rekey", "--reason", "annual rotation"]

    monkeypatch.setenv("LATTICE_SECRET_KEYS", f"b:{KEY_B}")
    monkeypatch.setenv("LATTICE_SECRET_ACTIVE_KEY", "b")
    assert await asyncio.to_thread(main, command) == 2
    assert "key ID(s) a" in capsys.readouterr().err

    monkeypatch.setenv("LATTICE_SECRET_KEYS", f"a:{KEY_A},b:{KEY_B}")
    assert await asyncio.to_thread(main, command) == 0
    out, err = capsys.readouterr()
    assert "halden: re-keyed 2, already on b 0" in out
    assert "veridian" not in out
    assert KEY_A not in out + err
    assert KEY_B not in out + err
    assert await asyncio.to_thread(main, command) == 0
    assert "re-keyed 0, already on b 2" in capsys.readouterr().out
    async with owner_db.tenant_snapshot(halden) as s:
        events = (
            await s.execute(text("SELECT actor, action, before, after, reason FROM audit_events"))
        ).all()
    assert len(events) == 1  # the repeat moved nothing and wrote nothing
    [event] = events
    assert event.action == "secret.rekeyed"
    assert event.before == {"key_ids": ["a"]}
    assert event.after == {"key_id": "b", "rekeyed": 2, "already_current": 0}
    assert event.reason == "annual rotation"
    # The old key can now be retired.
    monkeypatch.setenv("LATTICE_SECRET_KEYS", f"b:{KEY_B}")
    assert await asyncio.to_thread(main, command) == 0


def test_new_key_prints_a_usable_entry(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["secrets", "new-key", "--id", "k2026"]) == 0
    line = capsys.readouterr().out.strip()
    assert (
        Keyring.from_environment(
            {"LATTICE_SECRET_KEYS": line, "LATTICE_SECRET_ACTIVE_KEY": "k2026"}
        ).active
        == "k2026"
    )
    assert main(["secrets", "new-key", "--id", "Bad Id"]) == 2


async def test_sso_update_replaces_an_encrypted_client_secret(
    owner_db: Database, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    org = await create_org(owner_db, "halden")
    connection = await create_oidc_connection(owner_db, org)
    monkeypatch.setenv("LATTICE_MIGRATION_DATABASE_URL", OWNER_URL)
    monkeypatch.setenv("LATTICE_SECRET_KEYS", f"a:{KEY_A}")
    monkeypatch.setenv("LATTICE_SECRET_ACTIVE_KEY", "a")
    update = ["sso", "update", "--org", "halden", "--client-secret-stdin", "--reason", "rotate"]
    for value in (SECRET, SECRET + "-next"):
        monkeypatch.setattr("sys.stdin", io.StringIO(value + "\n"))
        assert await asyncio.to_thread(main, update) == 0
        out, err = capsys.readouterr()
        assert value not in out + err
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert await asyncio.to_thread(main, update) == 1
    assert "empty" in capsys.readouterr().err
    async with owner_db.tenant_snapshot(org) as s:
        ref: str = (
            await s.execute(text("SELECT client_secret_ref FROM oidc_connections"))
        ).scalar_one()
        secrets = (
            await s.execute(
                text(
                    "SELECT owner_id, destroyed_at IS NULL AS live FROM encrypted_secrets "
                    "ORDER BY created_at"
                )
            )
        ).all()
        events: list[Any] = list(
            (await s.execute(text("SELECT after FROM audit_events ORDER BY created_at")))
            .scalars()
            .all()
        )
    assert ref.startswith("vault:")
    assert [(r.owner_id, r.live) for r in secrets] == [(connection, False), (connection, True)]
    assert [e["client_secret"] for e in events] == ["encrypted", "encrypted (rotated)"]
    assert SECRET not in str(events)


async def _sign_in(
    client: httpx.AsyncClient, idp: FakeIdp, key: str, *, cancel: bool = False
) -> str:
    start = await client.get(f"/v1/auth/oidc/{key}/start")
    location = start.headers["location"]
    if location.startswith("/sign-in"):
        return location
    callback = idp.authorize(location, "sub-pat")
    path = callback.split("127.0.0.1:3000", 1)[1].removeprefix("/api")
    if cancel:
        path = path.split("?")[0] + "?state=" + path.split("state=")[1].split("&")[0]
        path += "&error=access_denied"
    done = await client.get(path)
    return str(done.headers["location"])


async def _facts(owner_db: Database, org: uuid.UUID) -> tuple[Any, Any]:
    async with owner_db.tenant_snapshot(org) as s:
        row = (
            await s.execute(text("SELECT last_success_at, last_failure_at FROM oidc_connections"))
        ).one()
    return row.last_success_at, row.last_failure_at


async def test_sign_in_records_the_setup_facts(owner_db: Database, app_client: AppFactory) -> None:
    org = await create_org(owner_db, "halden")
    key = "haldenloginkeyhaldenlogin"
    idp = FakeIdp()
    connection = await create_oidc_connection(owner_db, org, login_key=key, issuer=idp.issuer)
    await create_provisioned_member(owner_db, org, connection, "pat@halden.example", "sub-pat")

    def configure(app: FastAPI) -> None:
        app.state.oidc = OidcSignIn(
            app.state.db,
            OidcClient(
                timeout_seconds=5, require_https=False, transport=httpx.MockTransport(idp.handler)
            ),
            web_origin="http://127.0.0.1:3000",
            secrets=app.state.ask_dependencies.secrets,
        )

    async with app_client(configure) as client:
        # A person cancelling at the provider is not a provider failure.
        assert "sso_unavailable" in await _sign_in(client, idp, key, cancel=True)
        assert await _facts(owner_db, org) == (None, None)
        assert await _sign_in(client, idp, key) == "/ask"
        success, failure = await _facts(owner_db, org)
        assert success is not None
        assert failure is None
        idp.down = True
        assert "sso_unavailable" in await _sign_in(client, idp, key)
        success_after, failure = await _facts(owner_db, org)
        assert failure is not None
        assert success_after == success


async def test_a_vault_client_secret_that_cannot_be_opened_is_a_provider_failure(
    owner_db: Database, app_client: AppFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LATTICE_SECRET_KEYS", raising=False)
    monkeypatch.delenv("LATTICE_SECRET_ACTIVE_KEY", raising=False)
    org = await create_org(owner_db, "halden")
    key = "haldenloginkeyhaldenlogin"
    connection = await create_oidc_connection(owner_db, org, login_key=key, issuer=FakeIdp().issuer)
    await create_provisioned_member(owner_db, org, connection, "pat@halden.example", "sub-pat")
    async with owner_db.tenant_transaction(org) as s:
        reference = await store_secret(
            s,
            ring("a"),
            organization_id=org,
            owner_kind="oidc_client_secret",
            owner_id=connection,
            value=SecretStr(SECRET),
        )
        await s.execute(
            text("UPDATE oidc_connections SET client_secret_ref = :r"), {"r": reference}
        )
    idp = FakeIdp(client_secret=SECRET)

    def configure(app: FastAPI) -> None:  # the API without keys: it cannot open the secret
        app.state.oidc = OidcSignIn(
            app.state.db,
            OidcClient(
                timeout_seconds=5, require_https=False, transport=httpx.MockTransport(idp.handler)
            ),
            web_origin="http://127.0.0.1:3000",
            secrets=app.state.ask_dependencies.secrets,
        )

    async with app_client(configure) as client:
        assert "sso_unavailable" in await _sign_in(client, idp, key)
    assert (await _facts(owner_db, org))[1] is not None
    assert not [r for r in idp.requests if r.url.path.endswith("/token")]  # nothing sent


def test_production_workers_and_migration_jobs_refuse_master_keys(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LATTICE_ENV", "production")
    monkeypatch.setenv("LATTICE_SECRET_KEYS", f"a:{KEY_A}")
    monkeypatch.setenv("LATTICE_SECRET_ACTIVE_KEY", "a")
    monkeypatch.setenv("LATTICE_MIGRATION_DATABASE_URL", OWNER_URL)
    assert main(["check-config", "migrate"]) == 2
    err = capsys.readouterr().err
    assert "LATTICE_SECRET_KEYS" in err
    assert KEY_A not in err
    from lattice.vault.keys import misplaced_keys_problem

    assert misplaced_keys_problem("worker", True, {"LATTICE_SECRET_KEYS": "x"}) is not None


def test_check_config_api_refuses_a_malformed_keyring(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LATTICE_ENV", "test")
    monkeypatch.setenv("LATTICE_DATABASE_URL", "postgresql+asyncpg://x@localhost/x")
    monkeypatch.setenv("LATTICE_SECRET_KEYS", "a:not-a-key")
    monkeypatch.setenv("LATTICE_SECRET_ACTIVE_KEY", "a")
    assert main(["check-config", "api"]) == 2
    assert "not valid base64" in capsys.readouterr().err


async def test_the_api_refuses_to_start_without_a_stored_key_in_production(
    owner_db: Database, app_db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    org = await create_org(owner_db, "halden")
    await _store(owner_db, org, ring("a"))
    await verify_secret_keys(app_db, ring("a"), production=True)  # held: fine
    with pytest.raises(SecretKeysMissing, match="key ID\\(s\\) a"):
        await verify_secret_keys(app_db, ring("b", b=KEY_B), production=True)
    await verify_secret_keys(app_db, Keyring(), production=False)  # a warning outside
    assert "secret.keys_missing" in caplog.text


def test_create_app_refuses_a_malformed_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    from lattice.api.app import create_app

    from .conftest import make_settings

    monkeypatch.setenv("LATTICE_SECRET_KEYS", "a:" + KEY_A)
    monkeypatch.setenv("LATTICE_SECRET_ACTIVE_KEY", "missing")
    with pytest.raises(ConfigurationError, match="names a key"):
        create_app(make_settings())


@pytest.mark.parametrize(("token_status", "recorded"), [(400, False), (401, True), (500, True)])
async def test_only_provider_side_token_failures_mark_the_record(
    owner_db: Database, app_client: AppFactory, token_status: int, recorded: bool
) -> None:
    """Anyone can start a flow and come back with a bogus code (the token endpoint's 400):
    that is not the provider failing, so it never flips the administrators' record."""
    org = await create_org(owner_db, "halden")
    key = "haldenloginkeyhaldenlogin"
    idp = FakeIdp(token_status=token_status)
    connection = await create_oidc_connection(owner_db, org, login_key=key, issuer=idp.issuer)
    await create_provisioned_member(owner_db, org, connection, "pat@halden.example", "sub-pat")

    def configure(app: FastAPI) -> None:
        app.state.oidc = OidcSignIn(
            app.state.db,
            OidcClient(
                timeout_seconds=5, require_https=False, transport=httpx.MockTransport(idp.handler)
            ),
            web_origin="http://127.0.0.1:3000",
            secrets=app.state.ask_dependencies.secrets,
        )

    async with app_client(configure) as client:
        assert "sso_unavailable" in await _sign_in(client, idp, key)
    assert ((await _facts(owner_db, org))[1] is not None) is recorded
