"""Architectural boundary: only ``lattice.authz`` builds document-access conditions.

Two mechanical rules over every module of the ``lattice`` package outside
``lattice/authz/`` (future modules such as retrieval are covered automatically):

1. Import only the public contract: ``from lattice.authz import <name in __all__>``.
   Never a submodule (``lattice.authz.sql``, ``.models``, ...), so the tables that
   carry access state and the predicate internals are not reachable directly.
2. Never touch the access vocabulary: no attribute, keyword argument or SQL string
   naming ``sensitivity``, ``access_mode``, ``department_ids``, ``principal_type`` or
   the grant/group tables. Retrieval must call ``readable_documents_predicate``; it
   cannot re-create permission conditions ad hoc, even in raw SQL.

A module that legitimately *writes* access state (ingestion, Phase 4; access
management, Phase 9) needs an explicit, reviewed entry in ``ALLOWED`` below. No module
outside ``lattice.authz`` may build a *read* condition, allowance or not.

This replaces the import-linter contract named in the implementation plan: import
rules alone cannot see ``Document.sensitivity == "internal"`` written outside authz,
which is the failure this boundary exists to prevent.

The second half of the module pins ``POLICY_VERSION`` to the policy code.
"""

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

import lattice.authz
from lattice.authz import POLICY_VERSION

PACKAGE = Path(lattice.authz.__file__).resolve().parents[1]  # src/lattice
AUTHZ = PACKAGE / "authz"
PUBLIC = frozenset(lattice.authz.__all__)

ACCESS_ATTRIBUTES = frozenset({"sensitivity", "access_mode", "department_ids", "principal_type"})
ACCESS_NAMES = frozenset({"DocumentGrant", "AccessGroup", "AccessGroupMember"})
ACCESS_SQL = re.compile(
    r"\b(sensitivity|access_mode|department_ids|principal_type"
    r"|document_grants|access_groups|access_group_members)\b"
)

# module (relative to src/lattice, dotted) -> names it may use, with the reason.
ALLOWED: dict[str, frozenset[str]] = {
    # The operator seed writes admin-defined access groups and their members from the
    # organization manifest, upserting by the stable key (D-9). It writes no documents
    # or grants and builds no access conditions.
    "seed": frozenset({"AccessGroup", "AccessGroupMember"}),
    # The dataset's expected-access oracle maps raw fixture metadata to DocumentAccess
    # and asks the *reference* policy (readable, account_visible,
    # visible_document_account) who may read each record. It builds no SQL and no
    # access condition of its own; confining the vocabulary to this module keeps the
    # rest of lattice.dataset clean.
    "dataset.access": frozenset({"sensitivity", "access_mode", "department_ids"}),
    # --- Phase 4: ingestion writes access state; it never decides who may read. ---
    # The document-repository connector reports the classification its front matter
    # states, as an untrusted ``AccessHints`` value. No database, no condition.
    "connectors.markdown": frozenset({"sensitivity"}),
    # MAP ACCESS: turns untrusted hints into the access record to store (the same rules
    # as the dataset oracle, compared record by record in tests/ingestion). Pure
    # functions over a record and the source configuration; no SQL. The strings are the
    # keys of the canonical access hash.
    "ingestion.access": frozenset({"sensitivity", "access_mode", "department_ids"}),
    # The embedding decision asks the AI egress policy whether a document's
    # classification may be sent to the provider (SEC-10). A data policy, not
    # authorization: it never admits or hides a document for a reader.
    "ingestion.sync": frozenset({"sensitivity"}),
    # PERSIST: the one writer of a document's access columns, its source-origin grants
    # and source-synced groups and their members. Its reads of ``sensitivity`` in SQL
    # are egress-policy filters over chunks to embed or un-embed (never a read path).
    # It never touches admin-origin grants.
    "ingestion.store": frozenset(
        {
            "sensitivity",
            "access_mode",
            "department_ids",
            "principal_type",
            "document_grants",
            "access_groups",
            "access_group_members",
        }
    ),
    # Source disconnect: counts the grants and groups that the purge removes and bumps
    # ``authz_updated_at`` of members who lose a source-synced group. The purge itself is
    # the foreign-key cascade from ``sources``.
    "ingestion.lifecycle": frozenset({"document_grants", "access_groups", "access_group_members"}),
    # D-12: applies admin-grants.yaml as admin-origin membership grants, all or nothing.
    # Never reads or writes source-origin grants.
    "admin_grants": frozenset({"document_grants", "principal_type"}),
}


@dataclass(frozen=True)
class Violation:
    module: str
    line: int
    detail: str


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _absolute(module: str, node: ast.ImportFrom) -> str:
    """The absolute target of ``from X import ...`` inside ``lattice.<module>``."""
    if not node.level:
        return node.module or ""
    base = f"lattice.{module}".split(".")[: -node.level]
    return ".".join([*base, *([node.module] if node.module else [])])


def check_source(module: str, source: str) -> list[Violation]:
    tree = ast.parse(source)
    allowed = ALLOWED.get(module, frozenset())
    docstrings = _docstring_nodes(tree)
    found: list[Violation] = []

    def flag(node: ast.AST, detail: str) -> None:
        found.append(Violation(module, getattr(node, "lineno", 0), detail))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("lattice.authz."):
                    flag(node, f"imports submodule {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            target = _absolute(module, node)
            if target.startswith("lattice.authz."):
                flag(node, f"imports lattice.authz internals ({target})")
            elif target == "lattice.authz":
                for alias in node.names:
                    if alias.name not in PUBLIC:
                        flag(node, f"imports non-public lattice.authz.{alias.name}")
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name | ast.Attribute)
            and ast.unparse(node.value) in ("authz", "lattice.authz")
            and node.attr not in PUBLIC
        ):
            flag(node, f"reaches into lattice.authz.{node.attr}")
        elif isinstance(node, ast.Attribute) and node.attr in ACCESS_ATTRIBUTES - allowed:
            flag(node, f"uses access attribute .{node.attr}")
        elif isinstance(node, ast.keyword) and node.arg in ACCESS_ATTRIBUTES - allowed:
            flag(node, f"sets access attribute {node.arg}=")
        elif isinstance(node, ast.Name) and node.id in ACCESS_NAMES - allowed:
            flag(node, f"uses access table model {node.id}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            # Every access name in the string: an allowed name early in a statement
            # must not hide a disallowed one later in it.
            for name in dict.fromkeys(m.group(1) for m in ACCESS_SQL.finditer(node.value)):
                if name not in allowed:
                    flag(node, f"names access column/table {name!r} in a string")
    return found


def _modules_outside_authz() -> list[tuple[str, Path]]:
    return [
        (".".join(path.relative_to(PACKAGE).with_suffix("").parts), path)
        for path in sorted(PACKAGE.rglob("*.py"))
        if AUTHZ not in path.parents
    ]


def test_scan_covers_the_package() -> None:
    modules = {name for name, _ in _modules_outside_authz()}
    assert {"api.app", "identity.sessions", "seed", "db.engine"} <= modules
    # Phase 5: every retrieval path is scanned, and none has an allowance.
    retrieval = {
        "retrieval.exact",
        "retrieval.lexical",
        "retrieval.semantic",
        "retrieval.evidence",
        "retrieval.permitted",
        "retrieval.service",
        "retrieval.fusion",
        "retrieval.rerank",
        "evals.retrieval",
    }
    assert retrieval <= modules
    assert not retrieval & set(ALLOWED)


def test_only_authz_builds_document_access_conditions() -> None:
    violations = [
        v for name, path in _modules_outside_authz() for v in check_source(name, path.read_text())
    ]
    assert violations == []


@pytest.mark.parametrize(
    "source",
    [
        "from lattice.authz.sql import readable_documents_predicate",
        "import lattice.authz.models",
        "from lattice.authz import models",
        "from lattice.authz import _GRANTS",
        "from lattice.authz.policy import policy_allows",
        "q = select(Document.id).where(Document.sensitivity == 'internal')",
        "q = select(Document.id).where(Document.access_mode != 'grants_only')",
        "q = d.c.department_ids.overlap(ids)",
        "row = Document(sensitivity='internal', access_mode='policy')",
        "q = text('SELECT id FROM documents WHERE sensitivity = :s')",
        "q = text('SELECT 1 FROM document_grants g WHERE g.membership_id = :m')",
        "q = text('SELECT 1 FROM access_group_members')",
        "q = select(DocumentGrant)",
        "from ..authz.sql import DOCUMENTS",
        "from ..authz import policy",
        "from lattice import authz\nq = authz.sql.readable_documents_predicate",
        # Retrieval-shaped mistakes: a hand-written permission filter in a retriever.
        "q = select(CHUNKS.c.id).where(DOCUMENTS.c.sensitivity.in_(('internal',)))",
        "cond = documents.c.access_mode == 'policy'",
        "q = text('SELECT c.id FROM chunks c JOIN documents d ON d.id = c.document_id "
        "WHERE d.sensitivity = :s')",
        "q = select(Chunk.id).where(Document.department_ids.overlap(ids))",
    ],
)
def test_the_boundary_check_catches_violations(source: str) -> None:
    for module in ("retrieval.search", "retrieval.lexical", "retrieval.semantic"):
        assert check_source(module, source), (module, source)


def test_an_allowed_name_does_not_hide_a_disallowed_one_in_the_same_string() -> None:
    source = (
        "q = text('DELETE FROM document_grants WHERE group_id IN (SELECT id FROM access_groups)')"
    )
    assert [v.detail for v in check_source("admin_grants", source)] == [
        "names access column/table 'access_groups' in a string"
    ]


@pytest.mark.parametrize(
    "source",
    [
        "from lattice.authz import readable_documents_predicate, Document",
        "from ..authz import readable_documents_predicate",
        "from lattice import authz\nq = authz.readable_documents_predicate(ctx)",
        "q = select(Document.id).where(readable_documents_predicate(ctx))",
        '"""Docstrings may discuss sensitivity and access_mode."""',
        "def f():\n    '''Explains department_ids.'''\n    return 1",
    ],
)
def test_the_boundary_check_allows_the_public_contract(source: str) -> None:
    assert check_source("retrieval.search", source) == []


# --- POLICY_VERSION is tied to the policy code ---------------------------------------

POLICY_FILES = ("context.py", "policy.py", "sql.py", "resolve.py")

# One entry per released policy version. Changing the behaviour-bearing code of the
# modules above changes the fingerprint: bump POLICY_VERSION in lattice/authz/policy.py
# and add the new pair here (never edit an existing entry). Comments, docstrings and
# formatting do not affect the fingerprint.
POLICY_FINGERPRINTS = {
    "authz-1": "33ecc0dbf73c974351c7db1a4eb7d0366a53917782415a3bf3093641b8863499",
    # D-8: visible_document_account (structured account metadata of readable documents).
    "authz-2": "ddfdb0e29d3480d339e889a1481d7ef4c05c5c145bdff6fd32aa3da57ff5a846",
}


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                node.body = body[1:] or [ast.Pass()]
    return tree


def policy_fingerprint() -> str:
    digest = hashlib.sha256()
    for name in POLICY_FILES:
        tree = _strip_docstrings(ast.parse((AUTHZ / name).read_text()))
        digest.update(name.encode())
        digest.update(ast.dump(tree, annotate_fields=False).encode())
    return digest.hexdigest()


def test_policy_version_matches_the_policy_code() -> None:
    current = policy_fingerprint()
    assert POLICY_VERSION in POLICY_FINGERPRINTS, (
        f"Record the fingerprint of {POLICY_VERSION}: {current}"
    )
    assert POLICY_FINGERPRINTS[POLICY_VERSION] == current, (
        "The authorization policy code changed. Bump POLICY_VERSION in "
        f"lattice/authz/policy.py and record the new fingerprint here: {current}"
    )
