"""Recoverable credentials encrypted at rest (phase-12-plan C3; ``lattice.vault``).

AES-256-GCM with associated data bound to the organization, the secret row, the owner
record, the credential kind and the key ID: tampering, moving a ciphertext and using a
wrong or missing key all fail closed with fixed codes. The database holds ciphertext
only; the worker cannot read it; verification-only secrets stay hashes.
"""

import base64
import logging
import os
import uuid

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from lattice import metrics
from lattice.db.engine import Database
from lattice.providers.secrets import EnvironmentSecretStore, SecretUnavailable, parse_reference
from lattice.vault.keys import Keyring, KeyringError, misplaced_keys_problem, new_key_line
from lattice.vault.records import (
    RekeyRefused,
    destroy_secret,
    load_sealed,
    rekey_organization,
    store_secret,
    stored_key_ids,
)
from lattice.vault.sealing import (
    Binding,
    Sealed,
    SealedSecret,
    associated_data,
    open_sealed,
    seal,
    vault_secret_id,
)

from .factories import create_oidc_connection, create_org

VALUE = "sk-test-0123456789abcdefWXYZ"
KEY_A = base64.b64encode(b"A" * 32).decode()
KEY_B = base64.b64encode(b"B" * 32).decode()


def keyring(active: str = "a", **keys: str) -> Keyring:
    listed = keys or {"a": KEY_A}
    return Keyring.from_environment(
        {
            "LATTICE_SECRET_KEYS": ",".join(f"{k}:{v}" for k, v in listed.items()),
            "LATTICE_SECRET_ACTIVE_KEY": active,
        }
    )


def binding(**changes: object) -> Binding:
    values: dict[str, object] = {
        "organization_id": uuid.UUID(int=1),
        "secret_id": uuid.UUID(int=2),
        "owner_kind": "provider_api_key",
        "owner_id": uuid.UUID(int=3),
    }
    values.update(changes)
    return Binding(**values)  # type: ignore[arg-type]


def opened(ring: Keyring, sealed: Sealed, bound: Binding) -> str:
    return open_sealed(ring, SealedSecret(bound, sealed)).get_secret_value()


def code_of(ring: Keyring, sealed: Sealed | None, bound: Binding) -> str:
    with pytest.raises(SecretUnavailable) as failed:
        open_sealed(ring, SealedSecret(bound, sealed))
    assert VALUE not in str(failed.value)
    return failed.value.code


# --- keyring ------------------------------------------------------------------------------


def test_keyring_from_environment() -> None:
    ring = keyring("b", a=KEY_A, b=KEY_B)
    assert ring.active == "b"
    assert ring.ids == {"a", "b"}
    assert KEY_A not in repr(ring)
    assert KEY_B not in repr(ring)
    assert Keyring.from_environment({}).active is None


@pytest.mark.parametrize(
    ("environ", "fragment"),
    [
        ({"LATTICE_SECRET_KEYS": f"a:{KEY_A}"}, "LATTICE_SECRET_ACTIVE_KEY is not"),
        ({"LATTICE_SECRET_ACTIVE_KEY": "a"}, "LATTICE_SECRET_KEYS is not"),
        ({"LATTICE_SECRET_KEYS": f"a:{KEY_A}", "LATTICE_SECRET_ACTIVE_KEY": "b"}, "names a key"),
        ({"LATTICE_SECRET_KEYS": "a:not-base64!", "LATTICE_SECRET_ACTIVE_KEY": "a"}, "base64"),
        (
            {
                "LATTICE_SECRET_KEYS": f"a:{base64.b64encode(b'short').decode()}",
                "LATTICE_SECRET_ACTIVE_KEY": "a",
            },
            "exactly 32",
        ),
        ({"LATTICE_SECRET_KEYS": f"A B:{KEY_A}", "LATTICE_SECRET_ACTIVE_KEY": "a"}, "entry 1"),
        ({"LATTICE_SECRET_KEYS": KEY_A, "LATTICE_SECRET_ACTIVE_KEY": "a"}, "entry 1"),
        (
            {"LATTICE_SECRET_KEYS": f"a:{KEY_A},a:{KEY_B}", "LATTICE_SECRET_ACTIVE_KEY": "a"},
            "twice",
        ),
    ],
)
def test_bad_key_configuration_is_refused_without_material(
    environ: dict[str, str], fragment: str
) -> None:
    with pytest.raises(KeyringError, match=fragment) as refused:
        Keyring.from_environment(environ)
    assert KEY_A not in str(refused.value)
    assert KEY_B not in str(refused.value)


def test_new_key_line_round_trips() -> None:
    line = new_key_line("k2026", os.urandom(32))
    assert keyring("k2026", k2026=line.partition(":")[2]).active == "k2026"


def test_the_production_worker_refuses_master_keys() -> None:
    environ = {"LATTICE_SECRET_KEYS": f"a:{KEY_A}", "LATTICE_SECRET_ACTIVE_KEY": "a"}
    problem = misplaced_keys_problem("worker", True, environ)
    assert problem is not None
    assert "LATTICE_SECRET_KEYS" in problem
    assert KEY_A not in problem
    assert misplaced_keys_problem("worker", False, environ) is None
    assert misplaced_keys_problem("migrate", True, {}) is None


# --- sealing ------------------------------------------------------------------------------


def test_seal_and_open() -> None:
    ring = keyring()
    sealed = seal(ring, SecretStr(f"  {VALUE}\n"), binding())
    assert sealed.key_id == "a"
    assert sealed.nonce is not None
    assert len(sealed.nonce) == 12
    assert sealed.ciphertext is not None
    assert VALUE.encode() not in sealed.ciphertext
    assert opened(ring, sealed, binding()) == VALUE  # stripped on the way in
    again = seal(ring, SecretStr(VALUE), binding())
    assert again.nonce != sealed.nonce
    assert again.ciphertext != sealed.ciphertext


def test_the_active_key_is_used_and_older_keys_still_open() -> None:
    old = seal(keyring("a"), SecretStr(VALUE), binding())
    ring = keyring("b", a=KEY_A, b=KEY_B)
    assert seal(ring, SecretStr(VALUE), binding()).key_id == "b"
    assert opened(ring, old, binding()) == VALUE


def _flip(data: bytes, index: int) -> bytes:
    return data[:index] + bytes([data[index] ^ 1]) + data[index + 1 :]


def test_tampering_fails_closed() -> None:
    ring = keyring()
    sealed = seal(ring, SecretStr(VALUE), binding())
    assert sealed.ciphertext is not None
    assert sealed.nonce is not None
    tampered = [
        Sealed("a", sealed.nonce, _flip(sealed.ciphertext, 0)),  # ciphertext
        Sealed("a", sealed.nonce, _flip(sealed.ciphertext, len(sealed.ciphertext) - 1)),  # tag
        Sealed("a", _flip(sealed.nonce, 0), sealed.ciphertext),  # nonce
        Sealed("a", sealed.nonce, sealed.ciphertext[:-1]),  # truncated
    ]
    for each in tampered:
        assert code_of(ring, each, binding()) == "decrypt_failed"


@pytest.mark.parametrize(
    "changed",
    [
        {"organization_id": uuid.UUID(int=9)},  # moved to another organization
        {"secret_id": uuid.UUID(int=9)},  # copied into another secret row
        {"owner_id": uuid.UUID(int=9)},  # referenced by another record
        {"owner_kind": "oidc_client_secret"},  # used as another kind of credential
    ],
)
def test_associated_data_binds_the_ciphertext(changed: dict[str, object]) -> None:
    ring = keyring()
    sealed = seal(ring, SecretStr(VALUE), binding())
    assert code_of(ring, sealed, binding(**changed)) == "decrypt_failed"


def test_relabelled_key_id_fails() -> None:
    ring = keyring("a", a=KEY_A, b=KEY_B)
    sealed = seal(ring, SecretStr(VALUE), binding())
    assert sealed.nonce is not None
    assert sealed.ciphertext is not None
    assert code_of(ring, Sealed("b", sealed.nonce, sealed.ciphertext), binding()) == (
        "decrypt_failed"
    )


def test_wrong_and_missing_keys_fail_closed(caplog: pytest.LogCaptureFixture) -> None:
    sealed = seal(keyring(), SecretStr(VALUE), binding())
    wrong = keyring("a", a=KEY_B)  # same ID, other material
    before = metrics.SECRET_DECRYPT_FAILURES.labels(code="key_unavailable")._value.get()
    with caplog.at_level(logging.WARNING):
        assert code_of(wrong, sealed, binding()) == "decrypt_failed"
        assert code_of(keyring("b", b=KEY_B), sealed, binding()) == "key_unavailable"
        assert code_of(Keyring(), sealed, binding()) == "key_unavailable"
    after = metrics.SECRET_DECRYPT_FAILURES.labels(code="key_unavailable")._value.get()
    assert after == before + 2
    assert "secret.decrypt_failed" in caplog.text
    assert VALUE not in caplog.text
    assert KEY_A not in caplog.text
    with pytest.raises(SecretUnavailable, match="no_active_key"):
        seal(Keyring(), SecretStr(VALUE), binding())


def test_missing_and_destroyed_rows() -> None:
    assert code_of(keyring(), None, binding()) == "missing"
    assert code_of(keyring(), Sealed(None, None, None), binding()) == "destroyed"


def test_associated_data_layout() -> None:
    aad = associated_data(
        organization_id=uuid.UUID(int=1),
        secret_id=uuid.UUID(int=2),
        owner_kind="provider_api_key",
        owner_id=uuid.UUID(int=3),
        key_id="a",
    )
    assert aad.startswith(b"lattice-secret/v1|00000000-0000-0000-0000-000000000001|")
    assert aad.endswith(b"|provider_api_key|00000000-0000-0000-0000-000000000003|a")


def test_store_resolves_vault_references_only_with_keys_and_a_matching_row() -> None:
    ring = keyring()
    bound = binding()
    sealed = SealedSecret(bound, seal(ring, SecretStr(VALUE), bound))
    reference = f"vault:{bound.secret_id}"
    assert parse_reference(reference) == ("vault", str(bound.secret_id))
    with_keys = EnvironmentSecretStore(environ={}, keyring=ring)
    assert with_keys.resolve(reference, sealed).get_secret_value() == VALUE
    assert with_keys.available(reference, sealed)
    assert VALUE not in repr(with_keys)
    assert KEY_A not in repr(with_keys)
    # No keys (the worker, the web, migrations): nothing opens.
    with pytest.raises(SecretUnavailable, match="scheme_not_allowed"):
        EnvironmentSecretStore(environ={}).resolve(reference, sealed)
    # Without the row, or with a row loaded for another reference.
    with pytest.raises(SecretUnavailable, match="missing"):
        with_keys.resolve(reference)
    with pytest.raises(SecretUnavailable, match="missing"):
        with_keys.resolve(f"vault:{uuid.UUID(int=7)}", sealed)
    for bad in ("vault:", "vault:not-a-uuid", "vault:00000000000000000000000000000002"):
        with pytest.raises(SecretUnavailable, match="invalid_reference"):
            parse_reference(bad)
    assert vault_secret_id("env:LATTICE_PROVIDER_SECRET_X") is None


# --- database -----------------------------------------------------------------------------


async def _store(owner_db: Database, org: uuid.UUID, owner: uuid.UUID, ring: Keyring) -> str:
    async with owner_db.tenant_transaction(org) as s:
        return await store_secret(
            s,
            ring,
            organization_id=org,
            owner_kind="provider_api_key",
            owner_id=owner,
            value=SecretStr(VALUE),
        )


async def _open(
    db: Database, org: uuid.UUID, reference: str, owner: uuid.UUID, ring: Keyring
) -> str:
    async with db.tenant_snapshot(org) as s:
        loaded = await load_sealed(
            s, reference, organization_id=org, owner_kind="provider_api_key", owner_id=owner
        )
    assert loaded is not None
    return (
        EnvironmentSecretStore(environ={}, keyring=ring)
        .resolve(reference, loaded)
        .get_secret_value()
    )


async def test_stored_rows_hold_ciphertext_only(
    owner_db: Database, app_db: Database, superuser_engine: object
) -> None:
    org = await create_org(owner_db, "halden")
    owner = uuid.uuid4()
    ring = keyring()
    reference = await _store(owner_db, org, owner, ring)
    # The API role opens it (it presents the credential).
    assert await _open(app_db, org, reference, owner, ring) == VALUE
    async with owner_db.tenant_snapshot(org) as s:
        row = (await s.execute(text("SELECT * FROM encrypted_secrets"))).mappings().one()
    stored = b"".join(
        v if isinstance(v, bytes) else str(v).encode() for v in row.values() if v is not None
    )
    assert VALUE.encode() not in stored
    assert VALUE[-8:].encode() not in stored
    assert row["hint"] == "WXYZ"
    assert row["key_id"] == "a"


async def test_rows_are_tenant_isolated(owner_db: Database, app_db: Database) -> None:
    org = await create_org(owner_db, "halden")
    other = await create_org(owner_db, "veridian")
    owner = uuid.uuid4()
    reference = await _store(owner_db, org, owner, keyring())
    async with app_db.tenant_snapshot(other) as s:
        loaded = await load_sealed(
            s, reference, organization_id=other, owner_kind="provider_api_key", owner_id=owner
        )
        visible: int = (
            await s.execute(text("SELECT count(*) FROM encrypted_secrets"))
        ).scalar_one()
    assert visible == 0
    assert loaded is not None  # not readable from another org
    assert loaded.material is None


async def test_ciphertext_swapped_between_rows_fails(
    owner_db: Database, superuser_engine: object
) -> None:
    from sqlalchemy.ext.asyncio import AsyncEngine

    assert isinstance(superuser_engine, AsyncEngine)
    org = await create_org(owner_db, "halden")
    other = await create_org(owner_db, "veridian")
    ring = keyring()
    owner_a, owner_b, owner_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ref_a = await _store(owner_db, org, owner_a, ring)
    ref_b = await _store(owner_db, org, owner_b, ring)
    ref_c = await _store(owner_db, other, owner_c, ring)
    # An attacker with database write access copies A's ciphertext into B (same org) and
    # into C (another org).
    async with superuser_engine.begin() as conn:
        for target in (ref_b, ref_c):
            await conn.execute(
                text(
                    "UPDATE encrypted_secrets t SET key_id = s.key_id, nonce = s.nonce, "
                    "ciphertext = s.ciphertext FROM encrypted_secrets s "
                    "WHERE s.id = :a AND t.id = :t"
                ),
                {"a": vault_secret_id(ref_a), "t": vault_secret_id(target)},
            )
    with pytest.raises(SecretUnavailable, match="decrypt_failed"):
        await _open(owner_db, org, ref_b, owner_b, ring)
    with pytest.raises(SecretUnavailable, match="decrypt_failed"):
        await _open(owner_db, other, ref_c, owner_c, ring)
    # A record pointing at another record's secret does not open it either.
    with pytest.raises(SecretUnavailable, match="decrypt_failed"):
        await _open(owner_db, org, ref_a, owner_b, ring)
    assert await _open(owner_db, org, ref_a, owner_a, ring) == VALUE


async def test_destroyed_secrets_are_gone(owner_db: Database) -> None:
    org = await create_org(owner_db, "halden")
    owner = uuid.uuid4()
    ring = keyring()
    reference = await _store(owner_db, org, owner, ring)
    async with owner_db.tenant_transaction(org) as s:
        assert await destroy_secret(s, org, reference)
        assert not await destroy_secret(s, org, reference)  # once
        assert not await destroy_secret(s, org, "env:LATTICE_PROVIDER_SECRET_X")
        row = (
            await s.execute(
                text("SELECT nonce, ciphertext, hint, destroyed_at FROM encrypted_secrets")
            )
        ).one()
    assert row.nonce is None
    assert row.ciphertext is None
    assert row.hint is None
    assert row.destroyed_at is not None
    with pytest.raises(SecretUnavailable, match="destroyed"):
        await _open(owner_db, org, reference, owner, ring)
    # A second live secret for the same owner is allowed only after destruction.
    await _store(owner_db, org, owner, ring)
    with pytest.raises(DBAPIError, match="uq_encrypted_secrets_live_owner"):
        await _store(owner_db, org, owner, ring)


async def test_runtime_roles_cannot_write_and_the_worker_cannot_read(
    owner_db: Database, app_db: Database, worker_db: Database
) -> None:
    org = await create_org(owner_db, "halden")
    reference = await _store(owner_db, org, uuid.uuid4(), keyring())
    secret_id = vault_secret_id(reference)
    for sql in (
        "UPDATE encrypted_secrets SET key_id = 'b' WHERE id = :s",
        "DELETE FROM encrypted_secrets WHERE id = :s",
        "INSERT INTO encrypted_secrets (id, organization_id, owner_kind, owner_id, key_id, "
        "destroyed_at) VALUES (gen_random_uuid(), :o, 'provider_api_key', gen_random_uuid(), "
        "'a', now())",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_db.tenant_transaction(org) as s:
                await s.execute(text(sql), {"s": secret_id, "o": org})
    with pytest.raises(DBAPIError, match="permission denied"):
        async with worker_db.tenant_transaction(org) as s:
            await s.execute(text("SELECT ciphertext FROM encrypted_secrets"))


async def test_rekey_moves_every_live_ciphertext_to_the_active_key(owner_db: Database) -> None:
    org = await create_org(owner_db, "halden")
    other = await create_org(owner_db, "veridian")
    old = keyring("a")
    owners = {org: [uuid.uuid4(), uuid.uuid4()], other: [uuid.uuid4()]}
    refs = {
        o: [await _store(owner_db, o, owner, old) for owner in ids] for o, ids in owners.items()
    }
    async with owner_db.tenant_transaction(org) as s:
        await destroy_secret(s, org, refs[org][1])
    async with owner_db.unscoped_transaction() as s:
        assert await stored_key_ids(s) == {"a"}

    # Without the old key the rekey refuses before writing anything.
    with pytest.raises(RekeyRefused, match="key ID"):
        async with owner_db.tenant_transaction(org) as s:
            await rekey_organization(s, keyring("b", b=KEY_B), org)

    both = keyring("b", a=KEY_A, b=KEY_B)
    async with owner_db.tenant_transaction(org) as s:
        counts = await rekey_organization(s, both, org)
    assert (counts.rekeyed, counts.already_current, counts.from_keys) == (1, 0, {"a": 1})
    async with owner_db.tenant_transaction(org) as s:
        again = await rekey_organization(s, both, org)
    assert (again.rekeyed, again.already_current) == (0, 1)  # idempotent
    async with owner_db.unscoped_transaction() as s:
        assert await stored_key_ids(s) == {"a", "b"}  # veridian is not done yet
    async with owner_db.tenant_transaction(other) as s:
        await rekey_organization(s, both, other)
    async with owner_db.unscoped_transaction() as s:
        assert await stored_key_ids(s) == {"b"}
    # The old key can now be retired.
    new_only = keyring("b", b=KEY_B)
    assert await _open(owner_db, org, refs[org][0], owners[org][0], new_only) == VALUE
    assert await _open(owner_db, other, refs[other][0], owners[other][0], new_only) == VALUE


async def test_rekey_refuses_a_ciphertext_that_does_not_authenticate(
    owner_db: Database, superuser_engine: object
) -> None:
    from sqlalchemy.ext.asyncio import AsyncEngine

    assert isinstance(superuser_engine, AsyncEngine)
    org = await create_org(owner_db, "halden")
    good = await _store(owner_db, org, uuid.uuid4(), keyring("a"))
    bad = await _store(owner_db, org, uuid.uuid4(), keyring("a"))
    async with superuser_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE encrypted_secrets SET ciphertext = ciphertext || '\\x00'::bytea "
                "WHERE id = :s"
            ),
            {"s": vault_secret_id(bad)},
        )
    with pytest.raises(RekeyRefused, match="does not authenticate"):
        async with owner_db.tenant_transaction(org) as s:
            await rekey_organization(s, keyring("b", a=KEY_A, b=KEY_B), org)
    async with owner_db.tenant_snapshot(org) as s:
        ids: set[str] = set(
            (await s.execute(text("SELECT DISTINCT key_id FROM encrypted_secrets"))).scalars()
        )
    assert ids == {"a"}  # nothing was re-keyed
    assert good != bad


async def test_scim_tokens_stay_verification_only(owner_db: Database) -> None:
    """SCIM bearer tokens are one-way hashes: no ciphertext is created for them, and
    the table has no column that could hold a recoverable value."""
    from lattice.identity import admin

    org = await create_org(owner_db, "halden")
    await create_oidc_connection(owner_db, org)
    async with owner_db.tenant_transaction(org) as s:
        _, token = await admin.create_scim_token(s, org, reason="test", actor="operator:test")
        row = (await s.execute(text("SELECT * FROM scim_tokens"))).mappings().one()
        secrets_rows: int = (
            await s.execute(text("SELECT count(*) FROM encrypted_secrets"))
        ).scalar_one()
    assert secrets_rows == 0
    assert set(row) == {
        "id",
        "organization_id",
        "connection_id",
        "token_hash",
        "created_at",
        "revoked_at",
    }
    assert len(row["token_hash"]) == 32
    assert token.encode() not in row["token_hash"]
