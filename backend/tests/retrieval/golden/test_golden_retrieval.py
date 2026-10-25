"""The golden set against the real synthetic corpus (docs/evaluation-plan.md §5).

The harness (``lattice.evals.retrieval``) runs every golden item as its persona -- as
the runtime role, through RLS, context resolved per request -- and checks each stage
against the dataset's expected-access oracle, which never consults the database.

Security metrics must be exactly 0 for every item, with semantic retrieval on (fake
provider) and off. Recall is pinned by *regression floors* for this deterministic
configuration (fake embeddings); they are not the product's quality target, which
needs the real provider (docs/evaluation-plan.md, "Phase 5 results").

Since P13.1 both configurations are also compared **per item** with the committed
baselines (``evals/baselines/``, ``lattice.evals.baseline``): configuration A exactly
(it is platform-independent), configuration B exactly against the baseline of the
database server's architecture (pgvector's float32 distances differ between aarch64
and x86-64; evaluation-plan §15). An injected regression must be caught.
"""

import json
from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import text

from lattice.cli_retrieval import BASELINES
from lattice.dataset.lint import Dataset, load_dataset
from lattice.dataset.manifests import AS_OF
from lattice.db.engine import Database
from lattice.evals import baseline
from lattice.evals.retrieval import (
    Corpus,
    Identities,
    ItemResult,
    RetrievalReport,
    database_platform,
    evaluate_item,
    golden_version,
    select_items,
    summarize,
)
from lattice.providers.embedding import FakeEmbeddingProvider
from lattice.retrieval import DEFAULT_CONFIG, RetrievalConfig

from ...ingestion.conftest import DATA, GOLDEN

GOLDENS = select_items(load_dataset(DATA, GOLDEN))
ITEMS = [item.id for item in GOLDENS]
PHASE3 = {item.id for item in GOLDENS if item.label_source == "authored_phase3"}

# Measured on this corpus with this configuration (retrieval-6) over the 89 Phase 3 items
# (the historical benchmark; P13.2 added items are pinned per item by the baselines); a
# drop below fails CI. The per-item baselines below are the exact check, these the coarse.
FLOOR_R10_FAKE = 0.49  # measured 0.5123 on an aarch64 database, 0.5172 on x86-64
FLOOR_R10_LEXICAL_ONLY = 0.64  # measured 0.6679 on both


async def _run(
    app_db: Database,
    dataset: Dataset,
    identities: Identities,
    corpus: Corpus,
    provider: Any,
    config: RetrievalConfig = DEFAULT_CONFIG,
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
            config=config,
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
    fake = summarize([r for i, r in with_semantic.items() if i in PHASE3])["overall"]
    lexical = summarize([r for i, r in lexical_only.items() if i in PHASE3])["overall"]
    print(f"\nPhase 3 items R@10 fake {fake['recall@10']}, exact+lexical {lexical['recall@10']}")
    assert fake["items_with_targets"] == lexical["items_with_targets"] == 68
    assert fake["recall@10"] >= FLOOR_R10_FAKE
    assert lexical["recall@10"] >= FLOOR_R10_LEXICAL_ONLY
    # Every item is reported by split and label source, with denominators (EC-7, EC-8).
    s = _summary(lexical_only)
    assert set(s["by_split"]) == {"dev", "held_out"}
    assert sum(b["items"] for b in s["by_split"].values()) == len(ITEMS)
    assert s["by_label_source"]["authored_phase3"]["items_with_targets"] == 68


def test_semantic_ran_for_every_item(with_semantic: dict[str, ItemResult]) -> None:
    assert {r.semantic_status for r in with_semantic.values()} == {"matched"}


# --- same question, different persona -----------------------------------------------------


def _records(result: ItemResult, top: int = 10) -> set[str]:
    return {e["record"] for e in result.evidence if e["rank"] <= top}


def test_same_question_groups_give_each_persona_only_their_own_evidence(
    with_semantic: dict[str, ItemResult], dataset: Dataset
) -> None:
    groups = _summary(with_semantic)["same_question_groups"]
    assert len(groups) == len({i.same_question_group for i in GOLDENS if i.same_question_group})
    assert len(groups) >= 10
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
    assert len(veridian) == sum(1 for i in GOLDENS if i.organization == "veridian") >= 13
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
    assert len(injections) == sum(1 for i in GOLDENS if i.category == "prompt_injection") >= 4
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


# --- item-level baselines (P13.1) ------------------------------------------------------------


def _as_report(
    results: dict[str, ItemResult],
    *,
    provider: Any,
    corpus: Corpus,
    dataset: Dataset,
    platform: str,
    config: RetrievalConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    return baseline.normalize(
        RetrievalReport(
            generated_at="test",
            embedding_provider=None if provider is None else provider.provider,
            embedding_model=None if provider is None else provider.model,
            embedding_deployment=None if provider is None else provider.deployment,
            provider_usage={},
            config_version=config.version,
            rerank_version=config.rerank_version,
            corpus_embeddings=corpus.embeddings,
            summary=summarize(list(results.values())),
            items=list(results.values()),
            corpus_fingerprint=corpus.fingerprint,
            golden_version=golden_version(select_items(dataset)),
            database_platform=platform,
        )
    )


def test_configuration_a_reproduces_its_committed_baseline_exactly(
    lexical_only: dict[str, ItemResult], corpus: Corpus, dataset: Dataset
) -> None:
    run = _as_report(lexical_only, provider=None, corpus=corpus, dataset=dataset, platform="any")
    reference = baseline.load(BASELINES / "retrieval-A.json")
    result = baseline.compare(reference, run)
    assert result.verdict == "identical", baseline.render(result)
    # The Phase 3 items give P13.0's numbers (R@10 0.6679, MRR 0.6405) except for the
    # owner-approved scope-reachability correction of three items (evaluation-plan §16),
    # which drops unreachable targets: R@10 0.6765, rankings and MRR unchanged.
    phase3 = reference["summary"]["by_label_source"]["authored_phase3"]
    assert (phase3["recall@10"], phase3["mrr"]) == (0.6765, 0.6405)


async def test_configuration_b_reproduces_the_baseline_of_this_database_architecture(
    owner_db: Database, with_semantic: dict[str, ItemResult], corpus: Corpus, dataset: Dataset
) -> None:
    platform = await database_platform(owner_db)
    path = BASELINES / f"retrieval-B-{platform}.json"
    assert path.exists(), f"no configuration B baseline for a {platform} database server"
    run = _as_report(
        with_semantic,
        provider=FakeEmbeddingProvider(),
        corpus=corpus,
        dataset=dataset,
        platform=platform,
    )
    result = baseline.compare(baseline.load(path), run)
    assert result.verdict == "identical", baseline.render(result)


def test_configuration_b_differs_between_architectures_only_where_recorded() -> None:
    """The two B baselines differ in ranking (exact ties ordered by float32 rounding),
    and in exactly one target outcome; nothing else (evaluation-plan §15)."""
    arm = baseline.load(BASELINES / "retrieval-B-aarch64.json")
    x86 = baseline.load(BASELINES / "retrieval-B-x86_64.json")
    result = baseline.compare(arm, x86, cross_platform=True)
    assert result.verdict == "platform_variant"
    outcome = [f for f in result.findings if f.kind.startswith(("target_", "document_"))]
    assert {(f.item, f.kind) for f in outcome} == {
        ("xsrc-kestrel-outage-daniel", "target_gained@10"),
        ("xsrc-kestrel-outage-daniel", "target_rank_changed"),
        ("xsrc-kestrel-outage-daniel", "document_gained@10"),
    }, baseline.render(result)
    assert not baseline.compare(arm, x86).findings  # refused without --cross-platform
    assert baseline.compare(arm, x86).verdict == "incomparable"


async def test_compare_catches_an_injected_regression(
    app_db: Database,
    dataset: Dataset,
    identities: Identities,
    corpus: Corpus,
) -> None:
    """A real pipeline change -- the lexical channel cut from 50 to 10 candidates --
    under an unchanged configuration version is unexplained drift, with item-level
    regressions; under a declared version it is a regression to explain."""
    injected = replace(DEFAULT_CONFIG, k_lexical=10)
    results = await _run(app_db, dataset, identities, corpus, None, injected)
    reference = baseline.load(BASELINES / "retrieval-A.json")
    run = _as_report(
        results, provider=None, corpus=corpus, dataset=dataset, platform="any", config=injected
    )
    drift = baseline.compare(reference, run)
    assert drift.verdict == "drift"
    assert drift.exit_code == 1
    kinds = {f.kind for f in drift.regressions}
    assert "target_lost@10" in kinds, baseline.render(drift)
    lost = {f.item for f in drift.regressions if f.kind == "target_lost@10"}
    assert lost <= set(reference["items"])
    declared = baseline.compare(
        reference,
        _as_report(
            results,
            provider=None,
            corpus=corpus,
            dataset=dataset,
            platform="any",
            config=replace(injected, version="retrieval-6-k-lexical-10"),
        ),
    )
    assert declared.verdict == "regressed"
    assert declared.exit_code == 1
    assert declared.intended == ["config_version: retrieval-6 -> retrieval-6-k-lexical-10"]
    # Reports and comparisons carry keys and ranks, never question or evidence text.
    rendered = baseline.render(declared) + json.dumps(declared.to_json())
    assert not any(item.question in rendered for item in select_items(dataset))


# --- P13.2: exhaustive labels and hidden strong matches -------------------------------------


def test_precision_is_computed_for_exhaustive_items_only(
    lexical_only: dict[str, ItemResult],
) -> None:
    exhaustive = {i.id for i in GOLDENS if i.exhaustive}
    assert len(exhaustive) >= 8
    for item_id, result in lexical_only.items():
        if item_id in exhaustive:
            assert result.precision10 is not None, item_id
            assert result.precision10_ideal is not None, item_id
            assert 0 <= result.precision10 <= result.precision10_ideal <= 1, item_id
        else:
            assert result.precision10 is None, item_id
    precision = _summary(lexical_only)["precision@10"]
    assert precision["items"] == len(exhaustive)


# P13.2 items whose strongest match is a record the persona may not read (same tenant).
STRONG_TRAPS = [
    item.id
    for item in GOLDENS
    if item.label_source == "authored_phase13"
    and item.trap_phrases
    and {"unauthorized_more_relevant", "restricted_contains_answer"} & set(item.tags)
]


async def test_a_hidden_strong_match_changes_nothing_the_persona_receives(
    app_db: Database,
    owner_db: Database,
    dataset: Dataset,
    identities: Identities,
    corpus: Corpus,
    lexical_only: dict[str, ItemResult],
) -> None:
    """S4 / SEC-5 over the new traps: with the forbidden same-tenant records removed from
    the corpus, each persona's evidence, ranks, scores and channel counts are identical --
    the unreadable match never influenced matching or ranking."""
    from lattice.evals.retrieval import forbidden

    items = {item.id: item for item in GOLDENS}
    assert len(STRONG_TRAPS) >= 4
    for item_id in STRONG_TRAPS:
        item = items[item_id]
        org_id = identities.org_ids[item.organization]
        hidden = sorted(
            t.record.split(":", 1)[1]
            for t in forbidden(item)
            if t.organization == item.organization
        )
        assert hidden, item_id
        before = lexical_only[item_id]
        try:
            async with owner_db.tenant_transaction(org_id) as s:
                await s.execute(
                    text(
                        "UPDATE documents SET status = 'deleted' "
                        "WHERE organization_id = :o AND external_id = ANY(:ids)"
                    ),
                    {"o": org_id, "ids": hidden},
                )
            after = await evaluate_item(
                app_db,
                item,
                dataset=dataset,
                identities=identities,
                corpus=corpus,
                provider=None,
                config=DEFAULT_CONFIG,
                now=AS_OF,
            )
        finally:
            async with owner_db.tenant_transaction(org_id) as s:
                await s.execute(
                    text(
                        "UPDATE documents SET status = 'active' "
                        "WHERE organization_id = :o AND external_id = ANY(:ids)"
                    ),
                    {"o": org_id, "ids": hidden},
                )
        assert after.violations == []
        assert after.evidence == before.evidence, item_id
        assert after.counts == before.counts, item_id
