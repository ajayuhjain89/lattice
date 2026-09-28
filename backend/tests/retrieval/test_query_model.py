"""The typed query, its limits and deterministic analysis (no database)."""

import uuid

import pytest
from pydantic import ValidationError

from lattice.retrieval import RetrievalConfig, RetrievalQuery
from lattice.retrieval.models import MAX_LIMIT, MAX_QUERY_CHARS
from lattice.retrieval.query import (
    AccountNames,
    extract_identifiers,
    has_temporal_intent,
    mentioned_accounts,
    normalize,
)


@pytest.mark.parametrize(
    "field",
    [
        "organization_id",
        "organization",
        "membership_id",
        "clearance",
        "roles",
        "role_label",
        "department_ids",
        "group_ids",
        "account_scope",
        "assigned_account_ids",
        "grants",
        "is_admin",
    ],
)
def test_the_query_accepts_no_authorization_claims(field: str) -> None:
    with pytest.raises(ValidationError):
        RetrievalQuery.model_validate({"text": "refunds", field: "x"})
    with pytest.raises(ValidationError):
        RetrievalQuery.model_validate({"text": "refunds", "scope": {field: "x"}})


@pytest.mark.parametrize(
    "query",
    [
        {"text": ""},
        {"text": "x" * (MAX_QUERY_CHARS + 1)},
        {"text": "refunds", "limit": MAX_LIMIT + 1},
        {"text": "refunds", "limit": 0},
        {"text": "refunds", "classification": "public"},
        {"text": "refunds", "scope": {"source_types": ["email"]}},
        {"text": "refunds", "scope": {"since": "2026-09-02", "until": "2026-09-01"}},
        {"text": "refunds", "scope": {"account": ""}},
    ],
)
def test_abusive_or_malformed_queries_are_rejected(query: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as caught:
        RetrievalQuery.model_validate(query)
    # Validation messages never echo the submitted value.
    for error in caught.value.errors(include_input=False, include_url=False):
        assert "input" not in error


def test_limits_are_conservative() -> None:
    config = RetrievalConfig()
    assert MAX_QUERY_CHARS == 2000
    assert MAX_LIMIT == 20
    assert (config.k_exact, config.k_lexical, config.k_semantic, config.fused_pool) == (
        50,
        50,
        50,
        40,
    )
    assert config.max_chunks_per_document == 3
    with pytest.raises(ValueError, match="text search configuration"):
        RetrievalConfig(text_search_config="english'; DROP TABLE chunks; --")
    with pytest.raises(ValueError, match="k_lexical"):
        RetrievalConfig(k_lexical=10_000)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  What's   the status\tof HAL-4821?\n", "What's the status of HAL-4821?"),
        ("SCIM\u200b in Q3", "SCIM in Q3"),  # zero-width space removed
        ("Cafe\u0301 SLA", "Caf\u00e9 SLA"),  # NFC
        ("a\x00b\x07c", "a b c"),  # control characters become spaces
        ("\u2028\u2029 ", ""),  # Unicode line and paragraph separators
    ],
)
def test_normalization_is_conservative(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What's the status of HAL-4821?", ("HAL-4821",)),
        ("see hal-4821 and HAL-4702.", ("HAL-4821", "HAL-4702")),
        ("RFC-2026-17 supersedes the design", ("RFC-2026-17",)),
        ("INC-2026-031 postmortem", ("INC-2026-031",)),
        ("HAL-4821 HAL-4821 hal-4821", ("HAL-4821",)),
        ("SCIM in Q3 with SLA of 30 min", ()),
        ("text-embedding-3-small", ()),
        ("x-4821", ()),  # a one-letter prefix is not a reference
        ("HAL-4821x", ()),
    ],
)
def test_identifier_extraction(question: str, expected: tuple[str, ...]) -> None:
    assert extract_identifiers(question, limit=5) == expected


def test_identifier_extraction_is_bounded() -> None:
    question = " ".join(f"HAL-{n}" for n in range(1000, 1100))
    assert len(extract_identifiers(question, limit=5)) == 5


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What's the latest with Northpeak?", True),
        ("Is October still the plan?", True),
        ("What is the current refund window?", True),
        ("What happened in HAL-4533?", False),
        ("Who approved the Directory Sync decision?", False),
    ],
)
def test_temporal_intent(question: str, expected: bool) -> None:
    assert has_temporal_intent(question) is expected


def test_account_mentions_match_whole_names_and_aliases_of_given_accounts_only() -> None:
    acme = AccountNames(uuid.uuid4(), "acme-corp", "Acme Corp", ("Acme", "ACME Corp"))
    health = AccountNames(uuid.uuid4(), "acme-health", "Acme Health", ("Acme",))
    kestrel = AccountNames(uuid.uuid4(), "kestrel-bank", "Kestrel Bank", ("Kestrel",))
    assert mentioned_accounts("What did we promise Acme?", [acme, health, kestrel]) == (
        acme,
        health,
    )
    assert mentioned_accounts("What did we promise Acme?", [acme]) == (acme,)
    assert mentioned_accounts("acme's renewal", [kestrel, acme]) == (acme,)
    assert mentioned_accounts("Acmeville and Kestrels", [acme, kestrel]) == ()
    # An account that is not passed in (not visible) is plain text.
    assert mentioned_accounts("Kestrel Bank outage", [acme]) == ()
