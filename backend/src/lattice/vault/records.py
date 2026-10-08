"""``encrypted_secrets`` rows (migration 0016; phase-12-plan C3.2, C3.3).

A row holds ciphertext, its nonce and the ID of the master key that sealed it -- never
plaintext, never a key. Reads and writes run through row-level security bound to the
organization; the API role may only read (P12.1), the operator writes (``org create``,
``provider credential set --value-stdin``, ``sso update --client-secret-stdin``,
``secrets rekey``), the worker has no privilege at all.

* ``store_secret`` seals a new value and inserts the row; the caller stores
  ``vault:<id>`` in its own record (the ID is chosen first, so the associated data can
  name it).
* ``load_sealed`` reads a row for use, with the binding the *caller* expects.
* ``destroy_secret`` sets the ciphertext and nonce to NULL (rotation, revocation): live
  data no longer holds it; backups do until they expire (C3.5).
* ``rekey_organization`` re-seals every live ciphertext not under the active key; it
  refuses before writing anything when a stored key ID is not configured here, and an
  authentication failure aborts the organization's transaction.
"""

import uuid
from collections import Counter
from dataclasses import dataclass, field

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.vault.errors import SecretUnavailable
from lattice.vault.keys import Keyring
from lattice.vault.sealing import (
    Binding,
    OwnerKind,
    Sealed,
    SealedSecret,
    hint_for,
    normalise_value,
    open_sealed,
    reference_for,
    seal,
    vault_secret_id,
)

_LOAD_SQL = text(
    "SELECT key_id, nonce, ciphertext FROM encrypted_secrets WHERE organization_id = :o AND id = :s"
)


async def store_secret(
    session: AsyncSession,
    keyring: Keyring,
    *,
    organization_id: uuid.UUID,
    owner_kind: OwnerKind,
    owner_id: uuid.UUID,
    value: SecretStr,
) -> str:
    """Seal ``value`` for ``(organization, owner kind, owner)`` and insert it. Returns
    the reference ``vault:<id>`` to store in the owner's record."""
    value = normalise_value(value)
    secret_id = uuid.uuid4()
    binding = Binding(organization_id, secret_id, owner_kind, owner_id)
    sealed = seal(keyring, value, binding)
    await session.execute(
        text(
            "INSERT INTO encrypted_secrets (id, organization_id, owner_kind, owner_id, key_id, "
            "nonce, ciphertext, hint) VALUES (:id, :o, :kind, :owner, :key, :nonce, :ct, :hint)"
        ),
        {
            "id": secret_id,
            "o": organization_id,
            "kind": owner_kind,
            "owner": owner_id,
            "key": sealed.key_id,
            "nonce": sealed.nonce,
            "ct": sealed.ciphertext,
            "hint": hint_for(owner_kind, value),
        },
    )
    return reference_for(secret_id)


async def load_sealed(
    session: AsyncSession,
    reference: str,
    *,
    organization_id: uuid.UUID,
    owner_kind: OwnerKind,
    owner_id: uuid.UUID,
) -> SealedSecret | None:
    """The row ``reference`` names, bound to what the caller expects; None for a
    reference of another scheme. A missing row yields ``material=None``."""
    secret_id = vault_secret_id(reference)
    if secret_id is None:
        return None
    row = (await session.execute(_LOAD_SQL, {"o": organization_id, "s": secret_id})).one_or_none()
    material = (
        None
        if row is None
        else Sealed(
            key_id=row.key_id,
            nonce=None if row.nonce is None else bytes(row.nonce),
            ciphertext=None if row.ciphertext is None else bytes(row.ciphertext),
        )
    )
    return SealedSecret(Binding(organization_id, secret_id, owner_kind, owner_id), material)


async def destroy_secret(
    session: AsyncSession, organization_id: uuid.UUID, reference: str | None
) -> bool:
    """Destroy the ciphertext ``reference`` names (no-op for other schemes)."""
    secret_id = None if reference is None else vault_secret_id(reference)
    if secret_id is None:
        return False
    destroyed = await session.execute(
        text(
            "UPDATE encrypted_secrets SET nonce = NULL, ciphertext = NULL, hint = NULL, "
            "destroyed_at = now() WHERE organization_id = :o AND id = :s "
            "AND destroyed_at IS NULL RETURNING id"
        ),
        {"o": organization_id, "s": secret_id},
    )
    return destroyed.one_or_none() is not None


@dataclass(frozen=True)
class RekeyCounts:
    rekeyed: int
    already_current: int
    from_keys: dict[str, int] = field(default_factory=dict)


class RekeyRefused(Exception):
    """``secrets rekey`` cannot proceed; the message names key IDs only."""


async def rekey_organization(
    session: AsyncSession, keyring: Keyring, organization_id: uuid.UUID
) -> RekeyCounts:
    """Re-seal the organization's live ciphertexts under the active key, in the caller's
    transaction. Each is opened with its *own* binding (as stored) and sealed again
    with a new nonce; a ciphertext that does not authenticate aborts (nothing is
    written for the organization)."""
    if keyring.active is None:
        raise RekeyRefused("no active key is configured (LATTICE_SECRET_ACTIVE_KEY)")
    rows = (
        await session.execute(
            text(
                "SELECT id, owner_kind, owner_id, key_id, nonce, ciphertext "
                "FROM encrypted_secrets WHERE organization_id = :o AND destroyed_at IS NULL "
                "ORDER BY id FOR UPDATE"
            ),
            {"o": organization_id},
        )
    ).all()
    unknown = sorted({r.key_id for r in rows} - keyring.ids)
    if unknown:
        raise RekeyRefused(
            f"stored ciphertext uses key ID(s) {', '.join(unknown)} that this process does "
            "not hold; add them to LATTICE_SECRET_KEYS (decrypt-only) and run again"
        )
    moved: Counter[str] = Counter()
    current = 0
    for row in rows:
        if row.key_id == keyring.active:
            current += 1
            continue
        binding = Binding(organization_id, row.id, row.owner_kind, row.owner_id)
        stored = Sealed(row.key_id, bytes(row.nonce), bytes(row.ciphertext))
        try:
            value = open_sealed(keyring, SealedSecret(binding, stored))
        except SecretUnavailable as exc:
            raise RekeyRefused(
                f"secret {row.id} does not authenticate ({exc.code}); nothing was re-keyed "
                "for this organization"
            ) from None
        resealed = seal(keyring, value, binding)
        await session.execute(
            text(
                "UPDATE encrypted_secrets SET key_id = :k, nonce = :n, ciphertext = :c "
                "WHERE organization_id = :o AND id = :s"
            ),
            {
                "k": resealed.key_id,
                "n": resealed.nonce,
                "c": resealed.ciphertext,
                "o": organization_id,
                "s": row.id,
            },
        )
        moved[row.key_id] += 1
    return RekeyCounts(sum(moved.values()), current, dict(sorted(moved.items())))


async def stored_key_ids(session: AsyncSession) -> set[str]:
    """Key IDs of every live ciphertext in the database (all organizations), through
    ``lattice_secret_key_ids()`` -- IDs only, never material."""
    rows = await session.execute(text("SELECT key_id FROM lattice_secret_key_ids()"))
    found: list[str] = list(rows.scalars().all())
    return set(found)
