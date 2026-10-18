"""The egress lattice: the page's reading (``lattice.providers.lattice``) and the
database's (``lattice_egress_cells``, which decides escalations, D-34) are the same
function of the recorded configuration (P12.5; C6.2).

Property-style: many deterministic random configurations of the organization's settings
(none, a denied external class, ceilings, synthetic data) and of its records (purpose,
trust class, endpoint, enablement, approval and its level, data terms), each compared
cell for cell. Plus the cell reasons for the page, and the invocation rule's agreement:
a permitted cell is exactly what ``provider_decision`` (with the generation rules) allows.
"""

import random
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from lattice.db.engine import Database
from lattice.providers.egress import LEVELS_ORDER, OrgEgress
from lattice.providers.lattice import PolicyRecord, allowed_level, cells_for, lattice

from ..factories import create_org

LEVELS: tuple[str | None, ...] = (*LEVELS_ORDER, None)


def _random_settings(rng: random.Random) -> dict[str, Any] | None:
    if rng.random() < 0.15:
        return None  # no row: the defaults
    return {
        "external_egress_level": rng.choice(LEVELS),
        "organization_controlled_level": rng.choice(LEVELS),
        "data_origin": rng.choice(("customer", "synthetic")),
    }


def _random_record(rng: random.Random, index: int) -> dict[str, Any]:
    deployment = rng.choice(("local", "organization_controlled", "external_api"))
    return {
        "purpose": rng.choice(("generation", "embedding")),
        "provider": f"p{index}",
        "model": f"m{index}",
        "deployment": deployment,
        "endpoint": None
        if deployment == "external_api" or rng.random() < 0.3
        else f"https://h{index}.example",
        "approved_for_level": rng.choice(LEVELS_ORDER),
        "approved": rng.random() < 0.6,
        "enabled": rng.random() < 0.6,
        "training_opt_out": rng.random() < 0.7,
    }


async def _store(
    owner_db: Database, org: uuid.UUID, settings: dict[str, Any] | None, records: list[Any]
) -> list[PolicyRecord]:
    found: list[PolicyRecord] = []
    async with owner_db.tenant_transaction(org) as s:
        await s.execute(text("DELETE FROM model_providers"))
        await s.execute(text("DELETE FROM org_settings"))
        if settings is not None:
            await s.execute(
                text(
                    "INSERT INTO org_settings (organization_id, external_egress_level, "
                    "organization_controlled_level, data_origin) VALUES (:o, :e, :c, :d)"
                ),
                {
                    "o": org,
                    "e": settings["external_egress_level"],
                    "c": settings["organization_controlled_level"],
                    "d": settings["data_origin"],
                },
            )
        enabled_generation = False
        for r in records:
            enabled = r["enabled"]
            if r["purpose"] == "generation" and enabled:
                if enabled_generation:  # one enabled generation configuration
                    enabled = False
                enabled_generation = enabled_generation or enabled
            record_id: uuid.UUID = (
                await s.execute(
                    text(
                        "INSERT INTO model_providers (organization_id, purpose, provider, model, "
                        "deployment, endpoint, approved_for_level, approved_at, enabled, "
                        "retention_mode, training_opt_out) VALUES (:o, :u, :p, :m, :d, :e, :l, "
                        "CASE WHEN :a THEN now() END, :en, 'none', :t) RETURNING id"
                    ),
                    {
                        "o": org,
                        "u": r["purpose"],
                        "p": r["provider"],
                        "m": r["model"],
                        "d": r["deployment"],
                        "e": r["endpoint"],
                        "l": r["approved_for_level"],
                        "a": r["approved"],
                        "en": enabled,
                        "t": r["training_opt_out"],
                    },
                )
            ).scalar_one()
            found.append(
                PolicyRecord(
                    id=record_id,
                    purpose=r["purpose"],
                    provider=r["provider"],
                    model=r["model"],
                    deployment=r["deployment"],
                    endpoint=r["endpoint"],
                    approved_for_level=r["approved_for_level"],
                    explicitly_approved=r["approved"],
                    enabled=enabled,
                    training_opt_out=r["training_opt_out"],
                )
            )
    return found


async def _sql_cells(owner_db: Database, org: uuid.UUID) -> set[tuple[str, str, str]]:
    async with owner_db.tenant_transaction(org) as s:
        cells: list[dict[str, Any]] = (
            await s.execute(text("SELECT lattice_egress_cells(:o)"), {"o": org})
        ).scalar_one()
    return {(str(c["p"]), str(c["e"]), str(c["l"])) for c in cells}


def _egress(settings: dict[str, Any] | None) -> OrgEgress:
    if settings is None:
        return OrgEgress()
    return OrgEgress(
        settings["external_egress_level"],
        "internal",
        settings["data_origin"],
        settings["organization_controlled_level"],
    )


@pytest.mark.parametrize("seed", range(40))
async def test_the_database_and_the_page_read_the_same_lattice(
    owner_db: Database, seed: int
) -> None:
    rng = random.Random(seed)  # noqa: S311 - reproducible test data
    org = await create_org(owner_db, f"lattice-{seed}")
    settings = _random_settings(rng)
    records = await _store(
        owner_db, org, settings, [_random_record(rng, i) for i in range(rng.randint(1, 5))]
    )
    python = {c.key for c in lattice(_egress(settings), records) if c.permitted}
    assert await _sql_cells(owner_db, org) == python


def _record(**overrides: Any) -> PolicyRecord:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "purpose": "generation",
        "provider": "gemini",
        "model": "m",
        "deployment": "external_api",
        "endpoint": None,
        "approved_for_level": "confidential",
        "explicitly_approved": True,
        "enabled": True,
        "training_opt_out": True,
    }
    return PolicyRecord(**{**values, **overrides})


@pytest.mark.parametrize(
    ("org", "record", "reasons"),
    [
        (
            OrgEgress("restricted"),
            _record(),
            ("permitted", "permitted", "above_approval"),
        ),
        (
            OrgEgress("internal"),
            _record(),
            ("permitted", "above_ceiling", "above_ceiling"),
        ),
        (OrgEgress(None), _record(), ("deployment_not_allowed",) * 3),
        (OrgEgress("restricted"), _record(enabled=False), ("provider_disabled",) * 3),
        (OrgEgress("restricted"), _record(explicitly_approved=False), ("provider_unapproved",) * 3),
        (
            OrgEgress("restricted"),
            _record(training_opt_out=False),
            ("provider_data_use",) * 3,
        ),
        (
            OrgEgress("restricted", data_origin="synthetic"),
            _record(training_opt_out=False),
            ("permitted", "permitted", "above_approval"),
        ),
        (
            OrgEgress("internal"),
            _record(deployment="organization_controlled", endpoint="https://x"),
            ("deployment_not_allowed",) * 3,
        ),
        (
            OrgEgress("internal"),
            _record(deployment="local", approved_for_level="restricted"),
            ("permitted",) * 3,
        ),
        (
            OrgEgress("internal"),
            _record(
                purpose="embedding",
                explicitly_approved=False,
                enabled=False,
                approved_for_level="internal",
            ),
            ("permitted", "above_approval", "above_approval"),
        ),
        (OrgEgress("restricted"), _record(deployment="private"), ("deployment_unknown",) * 3),
    ],
)
def test_each_cell_says_why(org: OrgEgress, record: PolicyRecord, reasons: tuple[str, ...]) -> None:
    assert tuple(c.reason for c in cells_for(org, record)) == reasons


def test_a_credential_is_not_an_input() -> None:
    """``PolicyRecord`` has no credential field at all: a key cannot change a cell."""
    assert not any("credential" in name for name in PolicyRecord.__dataclass_fields__)


def test_allowed_level_matches_the_cells() -> None:
    rng = random.Random(7)  # noqa: S311 - reproducible test data
    for _ in range(200):
        org = OrgEgress(
            rng.choice(LEVELS),
            "internal",
            rng.choice(("customer", "synthetic")),
            rng.choice(LEVELS),
        )
        raw = _random_record(rng, 0)
        record = _record(
            purpose=raw["purpose"],
            deployment=raw["deployment"],
            endpoint=raw["endpoint"],
            approved_for_level=raw["approved_for_level"],
            explicitly_approved=raw["approved"],
            enabled=raw["enabled"],
            training_opt_out=raw["training_opt_out"],
        )
        level, _ = allowed_level(org, record)
        permitted = [c.level for c in cells_for(org, record) if c.permitted]
        expected = [] if level is None else list(LEVELS_ORDER[: LEVELS_ORDER.index(level) + 1])
        assert permitted == expected
