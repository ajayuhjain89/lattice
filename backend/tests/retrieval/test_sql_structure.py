"""Structural proof that every retrieval statement carries its security conditions.

Behavioural tests show unauthorized rows do not come back; these show *why*, by
reading the SQL each retriever compiles to. For the exact, lexical, semantic and
evidence statements, the WHERE clause must contain:

* the authorization predicate exactly as ``lattice.authz.readable_documents_predicate``
  compiles it for the same context (so it cannot be weakened, rewritten or dropped);
* the tenant condition on chunks and documents, bound to the reader's organization;
* the lifecycle condition (``documents.status = 'active'``);
* for the semantic statement, the vector-validity and embedding-space conditions.

A developer who removes or edits the predicate in one path breaks this immediately.
"""

import re
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import ClauseElement, FromClause, Select, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import visitors

from lattice.authz import (
    POLICY_VERSION,
    AuthorizationContext,
    readable_documents_predicate,
    visible_accounts_predicate,
)
from lattice.retrieval.evidence import QuestionTerms, evidence_statement
from lattice.retrieval.exact import exact_statement
from lattice.retrieval.lexical import LexicalWeights, lexical_statement
from lattice.retrieval.models import RetrievalConfig
from lattice.retrieval.permitted import DOCUMENTS, VISIBLE_ACCOUNT, ResolvedScope
from lattice.retrieval.records import coverage_statement
from lattice.retrieval.semantic import semantic_statement
from lattice.retrieval.terms import question_statistics_statement, term_statistics_statement

DIALECT = postgresql.dialect()  # type: ignore[no-untyped-call]
CONFIG = RetrievalConfig()
PATHS = [
    "exact",
    "lexical",
    "lexical_weighted",
    "semantic",
    "evidence",
    "evidence_terms",
    "term_statistics",
    "question_statistics",
    "record_coverage",
]
WEIGHTED = RetrievalConfig(lexical_term_weights="idf", lexical_title_factor=0.5)


def _ctx(scope: str = "assigned", clearance: str = "internal") -> AuthorizationContext:
    return AuthorizationContext(
        organization_id=uuid.uuid4(),
        membership_id=uuid.uuid4(),
        membership_status="active",
        clearance=clearance,  # type: ignore[arg-type]
        account_scope=scope,  # type: ignore[arg-type]
        assigned_account_ids=frozenset({uuid.uuid4()}),
        department_ids=frozenset({uuid.uuid4()}),
        group_ids=frozenset({uuid.uuid4()}),
        authz_updated_at=datetime(2026, 9, 27, tzinfo=UTC),
        policy_version=POLICY_VERSION,
    )


def sql(clause: ClauseElement) -> str:
    """Compiled SQL with bind parameter names erased and whitespace collapsed."""
    text = str(clause.compile(dialect=DIALECT))
    text = re.sub(r"%\(\w+\)s|__\[POSTCOMPILE_\w+\]", "?", text)
    return re.sub(r"\s+", " ", text).strip()


def predicate_sql(ctx: AuthorizationContext, documents: FromClause = DOCUMENTS) -> str:
    """The authz predicate as it compiles when correlated to ``documents`` (the grant
    EXISTS refers to the enclosing row), which is how every retriever embeds it."""
    compiled = sql(select(documents.c.id).where(readable_documents_predicate(ctx, documents)))
    return compiled.split(" WHERE ", 1)[1]


def where_contains(statement: Select[tuple[object, ...]], expected: ClauseElement) -> bool:
    """Structural comparison (SQLAlchemy ``compare``: same operators, columns and bound
    values) against every element of the statement's own WHERE clause."""
    assert statement.whereclause is not None
    return any(
        isinstance(e, ClauseElement) and e.compare(expected)
        for e in visitors.iterate(statement.whereclause)
    )


def where_clause(statement: Select[tuple[object, ...]]) -> str:
    """The statement's own WHERE clause as text (for plain column conditions)."""
    assert statement.whereclause is not None
    return sql(statement.whereclause)


def statements(ctx: AuthorizationContext) -> dict[str, Select[tuple[object, ...]]]:
    scope = ResolvedScope()
    return {
        "exact": exact_statement(ctx, scope, ["HAL-4821"], CONFIG),
        "lexical": lexical_statement(
            ctx,
            scope,
            "refund window",
            CONFIG,
            identifiers=["HAL-4821"],
            mentioned_account_ids=[uuid.uuid4()],
        ),
        "semantic": semantic_statement(ctx, scope, [0.1] * 1024, "text-embedding-3-small", CONFIG),
        "evidence": evidence_statement(ctx, [uuid.uuid4()]),
        # P13.3: hydration with the question's term coverage, and the gate's statistics.
        "evidence_terms": evidence_statement(
            ctx, [uuid.uuid4()], QuestionTerms("refund window", "Acme Corp Acme", "english")
        ),
        "term_statistics": term_statistics_statement(ctx, ["refund", "window"], [uuid.uuid4()]),
        # P13.4: weighted lexical ranking and the question's statistics it is built from.
        "lexical_weighted": lexical_statement(
            ctx,
            scope,
            "refund window",
            WEIGHTED,
            identifiers=["HAL-4821"],
            mentioned_account_ids=[uuid.uuid4()],
            weights=LexicalWeights({"refund": 2.5, "window": 1.5}, account=0.5),
        ),
        "question_statistics": question_statistics_statement(
            ctx, "refund window", "Acme Corp Acme", "english", [uuid.uuid4()]
        ),
        # F5.2: rerank_v2's lexeme coverage of the pooled chunks.
        "record_coverage": coverage_statement(
            ctx, [uuid.uuid4()], QuestionTerms("refund window", "Acme Corp Acme", "english")
        ),
    }


@pytest.mark.parametrize("scope", ["assigned", "all"])
@pytest.mark.parametrize("path", PATHS)
def test_every_statement_contains_the_authz_predicate_tenant_and_lifecycle(
    path: str, scope: str
) -> None:
    ctx = _ctx(scope)
    statement = statements(ctx)[path]
    where = where_clause(statement)
    assert where_contains(statement, readable_documents_predicate(ctx, DOCUMENTS)), (
        f"{path}: authorization predicate missing or altered"
    )
    # ... and it is this reader's predicate, not another context's.
    other = ctx.model_copy(update={"clearance": "confidential"})
    assert not where_contains(statement, readable_documents_predicate(other, DOCUMENTS))
    # The compiled SQL contains it verbatim, correlated to the searched documents.
    assert predicate_sql(ctx) in sql(statement), path
    assert "chunks.organization_id = ?" in where, f"{path}: chunk tenant condition"
    assert "documents.organization_id = ?" in where, f"{path}: document tenant condition"
    assert "documents.status = ?" in where, f"{path}: lifecycle condition"
    # The tenant values are the reader's organization, bound as parameters.
    params = statement.compile(dialect=DIALECT).params
    org_values = [v for v in params.values() if v == ctx.organization_id]
    assert len(org_values) >= 3, path
    assert "active" in params.values()


def test_the_predicate_is_the_authz_modules_own_sql() -> None:
    # Sanity: the fragment we search for really is the policy (grants, clearance,
    # account scope, departments), not something trivially present anyway.
    predicate = predicate_sql(_ctx())
    for fragment in ("EXISTS", "document_grants", "access_mode", "department_ids", "account_id"):
        assert fragment in predicate


@pytest.mark.parametrize("path", PATHS)
def test_the_structured_account_is_joined_through_account_visibility_only(path: str) -> None:
    ctx = _ctx()
    compiled = sql(statements(ctx)[path])
    visible = sql(visible_accounts_predicate(ctx, VISIBLE_ACCOUNT))
    assert "LEFT OUTER JOIN accounts AS visible_account ON" in compiled
    assert visible in compiled
    assert "JOIN accounts ON" not in compiled  # no other, unguarded account join


def test_the_semantic_statement_selects_valid_vectors_of_one_space_only() -> None:
    where = where_clause(statements(_ctx())["semantic"])
    assert "chunks.embedding_status = ?" in where
    assert "chunks.embedding IS NOT NULL" in where
    assert "chunks.embedding_model = ?" in where
    params = statements(_ctx())["semantic"].compile(dialect=DIALECT).params
    assert "embedded" in params.values()
    assert "text-embedding-3-small" in params.values()


def test_the_evidence_statement_names_supersession_links_through_the_predicate() -> None:
    ctx = _ctx()
    compiled = sql(statements(ctx)["evidence"])
    for alias in ("predecessor", "successor"):
        assert predicate_sql(ctx, DOCUMENTS.alias(alias)) in compiled, alias


def test_retrieval_statements_carry_no_client_supplied_organization() -> None:
    # The only organization values bound are the context's.
    ctx = _ctx()
    for path, statement in statements(ctx).items():
        params = statement.compile(dialect=DIALECT).params
        uuids = {v for v in params.values() if isinstance(v, uuid.UUID)}
        assert ctx.organization_id in uuids, path
