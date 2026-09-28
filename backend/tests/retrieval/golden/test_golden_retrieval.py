"""The golden set against the real synthetic corpus (docs/evaluation-plan.md §5).

The harness (``lattice.evals.retrieval``) runs every golden item as its persona -- as
the runtime role, through RLS, context resolved per request -- and checks each stage
against the dataset's expected-access oracle, which never consults the database.

Security metrics must be exactly 0 for every item, with semantic retrieval on (fake
provider) and off. Recall is pinned by *regression floors* for this deterministic
configuration (fake embeddings); they are not the product's quality target, which
needs the real provider (docs/evaluation-plan.md, "Phase 5 results").
"""

from typing import Any

import pytest

from lattice.dataset.lint import Dataset, load_dataset
from lattice.dataset.manifests import AS_OF
from lattice.db.engine import Database
from lattice.evals.retrieval import (
    Corpus,
    Identities,
    ItemResult,
    evaluate_item,
    select_items,
    summarize,
)
from lattice.providers.embedding import FakeEmbeddingProvider
from lattice.retrieval import DEFAULT_CONFIG

from ...ingestion.conftest import DATA, GOLDEN

ITEMS = [item.id for item in select_items(load_dataset(DATA, GOLDEN))]

# Measured on this corpus with this configuration (retrieval-6); a drop below fails CI.
FLOOR_R10_FAKE = 0.49  # measured 0.512
FLOOR_R10_LEXICAL_ONLY = 0.64  # measured 0.668


async def _run(
    app_db: Database,
    dataset: Dataset,
    identities: Identities,
    corpus: Corpus,
    provider: Any,
) -> dict[str, ItemResult]:
    results = {}
    for item in select_items(dataset):
        results[item.id] = await evaluate_item(
            app_db,
            item,
            dataset=dataset,
            identities=identities,
            corpus=corpus,
            provider=provider,
            config=DEFAULT_CONFIG,
            now=AS_OF,
        )
    return results


@pytest.fixture(scope="package")
async def with_semantic(
    app_db: Database, dataset: Dataset, identities: Identities, corpus: Corpus
) -> dict[str, ItemResult]:
    return await _run(app_db, dataset, identities, corpus, FakeEmbeddingProvider())


@pytest.fixture(scope="package")
async def lexical_only(
    app_db: Database, dataset: Dataset, identities: Identities, corpus: Corpus
) -> dict[str, ItemResult]:
    return await _run(app_db, dataset, identities, corpus, None)


@pytest.mark.parametrize("item_id", ITEMS)
async def test_no_item_retrieves_anything_unauthorized(
    item_id: str, with_semantic: dict[str, ItemResult], lexical_only: dict[str, ItemResult]
) -> None:
    for results in (with_semantic, lexical_only):
        result = results[item_id]
        assert result.error is None, result.error
        assert result.violations == [], result.violations


def _summary(results: dict[str, ItemResult]) -> dict[str, Any]:
    return summarize(list(results.values()))


def test_security_metrics_are_zero(
    with_semantic: dict[str, ItemResult], lexical_only: dict[str, ItemResult]
) -> None:
    for results in (with_semantic, lexical_only):
        security = _summary(results)["security"]
        assert security == dict.fromkeys(security, 0)


def test_recall_does_not_regress(
    with_semantic: dict[str, ItemResult], lexical_only: dict[str, ItemResult]
) -> None:
    fake = _summary(with_semantic)["overall"]
    lexical = _summary(lexical_only)["overall"]
    print(f"\nR@10 fake {fake['recall@10']}, exact+lexical {lexical['recall@10']}")
    assert fake["items_with_targets"] == lexical["items_with_targets"] == 68
    assert fake["recall@10"] >= FLOOR_R10_FAKE
    assert lexical["recall@10"] >= FLOOR_R10_LEXICAL_ONLY


def test_semantic_ran_for_every_item(with_semantic: dict[str, ItemResult]) -> None:
    assert {r.semantic_status for r in with_semantic.values()} == {"matched"}


# --- same question, different persona -----------------------------------------------------


def _records(result: ItemResult, top: int = 10) -> set[str]:
    return {e["record"] for e in result.evidence if e["rank"] <= top}


def test_same_question_groups_give_each_persona_only_their_own_evidence(
    with_semantic: dict[str, ItemResult], dataset: Dataset
) -> None:
    groups = _summary(with_semantic)["same_question_groups"]
    assert len(groups) == 10
    differing = 0
    for group, g in groups.items():
        personas = g["personas"]
        differing += bool(g["distinct_evidence_sets"])
        for persona, p in personas.items():
            assert p["violations"] == 0, (group, persona)
            org = with_semantic[p["item"]].organization
            world = dataset.worlds[org]
            for other, q in personas.items():
                if with_semantic[q["item"]].organization != org:
                    continue  # record keys are per organization; isolation is checked above
                # A record one persona retrieved that another may not read never
                # appears for the other.
                for record in set(p["evidence_records"]) - set(q["evidence_records"]):
                    if not world.can_read(other, world.raw.records[record]):
                        assert record not in q["evidence_records"], (group, other, record)
            for record in p["evidence_records"]:
                assert world.can_read(persona, world.raw.records[record]), (group, persona, record)
    # Permissions really change the evidence in most groups.
    assert differing >= 6


async def test_one_readers_request_never_changes_anothers(
    app_db: Database,
    dataset: Dataset,
    identities: Identities,
    corpus: Corpus,
    with_semantic: dict[str, ItemResult],
) -> None:
    items = {item.id: item for item in select_items(dataset)}
    order = [
        "egress-acme-commitments-grace",
        "visibility-commitments-maya",
        "visibility-commitments-daniel",
        "visibility-commitments-priya",
    ]
    for item_id in reversed(order):
        again = await evaluate_item(
            app_db,
            items[item_id],
            dataset=dataset,
            identities=identities,
            corpus=corpus,
            provider=FakeEmbeddingProvider(),
            config=DEFAULT_CONFIG,
            now=AS_OF,
        )
        assert again.evidence == with_semantic[item_id].evidence, item_id


async def test_the_more_privileged_reader_gets_additional_evidence(
    app_db: Database,
    dataset: Dataset,
    identities: Identities,
    corpus: Corpus,
    with_semantic: dict[str, ItemResult],
    lexical_only: dict[str, ItemResult],
) -> None:
    items = {item.id: item for item in select_items(dataset)}
    # Lexical only: the confidential notes have no vector under the default egress
    # policy, and the fake provider's noise would only blur the comparison.
    arjun = lexical_only["egress-acme-offer-arjun"]
    assert "doc:acme-exec-escalation-notes" in _records(arjun)
    as_maya = items["egress-acme-offer-arjun"].model_copy(
        update={"persona": "maya", "id": "offer-as-maya"}
    )
    maya = await evaluate_item(
        app_db,
        as_maya,
        dataset=dataset,
        identities=identities,
        corpus=corpus,
        provider=FakeEmbeddingProvider(),
        config=DEFAULT_CONFIG,
        now=AS_OF,
    )
    assert maya.violations == []
    assert "doc:acme-exec-escalation-notes" not in _records(maya, 20)
    assert _records(arjun) - _records(maya, 20)
    daniel = with_semantic["visibility-commitments-daniel"]
    # Daniel (no Acme scope) sees no account-scoped Acme record at all.
    assert not any(r.startswith("doc:acme-") for r in _records(daniel, 20))
    assert "ticket:HAL-4821" not in _records(daniel, 20)


# --- adversarial unauthorized matches ------------------------------------------------------

ADVERSARIAL = [
    item.id
    for item in select_items(load_dataset(DATA, GOLDEN))
    if item.trap_phrases
    or {"unauthorized_more_relevant", "restricted_contains_answer"} & set(item.tags)
]


@pytest.mark.parametrize("item_id", ADVERSARIAL)
async def test_the_strongest_unauthorized_match_never_enters_any_stage(
    item_id: str,
    with_semantic: dict[str, ItemResult],
) -> None:
    result = with_semantic[item_id]
    assert [
        v for v in result.violations if v["kind"] in ("unauthorized", "must_not_retrieve")
    ] == []


async def test_the_withheld_evidence_really_was_a_strong_match(
    app_db: Database, dataset: Dataset, identities: Identities, corpus: Corpus
) -> None:
    """For each adversarial item, ask the same question as a persona who *may* read the
    forbidden record: most of the time it is retrieved -- so for the original persona it
    was a real, strong match that the predicate kept out, not an irrelevant record."""
    from lattice.dataset.manifests import GoldenItem
    from lattice.evals.retrieval import forbidden

    items = {item.id: item for item in select_items(dataset)}
    shown, tried = 0, 0
    for item_id in ADVERSARIAL:
        item: GoldenItem = items[item_id]
        world = dataset.worlds[item.organization]
        for target in forbidden(item):
            if target.organization != item.organization:
                continue
            record = world.raw.records.get(target.record)
            readers = sorted(world.readers(record)) if record else []
            if not readers:
                continue
            tried += 1
            privileged = item.model_copy(update={"persona": readers[0], "expected": item.expected})
            result = await evaluate_item(
                app_db,
                privileged,
                dataset=dataset,
                identities=identities,
                corpus=corpus,
                provider=FakeEmbeddingProvider(),
                config=DEFAULT_CONFIG,
                now=AS_OF,
            )
            if target.record in {e["record"] for e in result.evidence}:
                shown += 1
    print(f"\nforbidden records retrieved for a reader who may see them: {shown}/{tried}")
    assert tried >= 20
    assert shown / tried >= 0.5


# --- cross-organization collisions ------------------------------------------------------


def test_cross_org_items_never_see_the_other_tenant(with_semantic: dict[str, ItemResult]) -> None:
    for item_id, result in with_semantic.items():
        assert not [v for v in result.violations if v["kind"] == "cross_org"], item_id
    veridian = [r for r in with_semantic.values() if r.organization == "veridian"]
    assert len(veridian) == 3
    for r in veridian:
        assert all(not e["record"].startswith("veridian/") for e in r.evidence)


# --- D-8 / D-10 ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "item_id", ["visibility-kestrel-latest-maya", "visibility-kestrel-outage-maya"]
)
def test_granted_rca_is_evidence_without_its_hidden_account(
    item_id: str, with_semantic: dict[str, ItemResult]
) -> None:
    result = with_semantic[item_id]
    rca = [e for e in result.evidence if e["record"] == "doc:kestrel-sso-incident-customer-rca"]
    assert rca, "the granted RCA is readable evidence for Maya"
    assert all(e["account"] is None for e in rca)
    assert all(e["account"] != "kestrel-bank" for e in result.evidence)


# --- prompt injection is data ----------------------------------------------------------------


def test_injection_items_retrieve_their_evidence_as_plain_data(
    with_semantic: dict[str, ItemResult], dataset: Dataset
) -> None:
    injections = [r for r in with_semantic.values() if r.category == "prompt_injection"]
    assert len(injections) == 4
    for r in injections:
        assert r.violations == []
    # Injection-bearing records are returned as ordinary evidence when relevant...
    fixtures = {i.record.split("#")[0] for i in dataset.ground_truth["halden"].injections}
    returned = {e["record"] for r in injections for e in r.evidence}
    assert fixtures & returned
    # ...and change nothing: identifiers come only from the question (the HAL-9999 an
    # injected message asks for is never looked up).
    cite = with_semantic["injection-cite-hal-9999"]
    assert cite.counts["exact"] == 0
