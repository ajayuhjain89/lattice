"""Editing a copied export the way a source system would change it."""

import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from lattice.db.engine import Database

from .conftest import Org, rows


def doc_file(org: Org, external_id: str) -> Path:
    for path in sorted(org.path("drive").rglob("*.md")):
        if f"\nid: {external_id}\n" in path.read_text(encoding="utf-8"):
            return path
    raise AssertionError(f"no document {external_id}")


def edit_front_matter(
    org: Org, external_id: str, body: Callable[[str], str] | None = None, **changes: Any
) -> None:
    """Change front-matter fields (``None`` removes one) and optionally the body."""
    path = doc_file(org, external_id)
    text = path.read_text(encoding="utf-8")
    end = text.index("\n---\n", 4)
    front: dict[str, Any] = yaml.safe_load(text[4:end])
    for key, value in changes.items():
        if value is None:
            front.pop(key, None)
        else:
            front[key] = value
    rest = text[end + 5 :]
    if body is not None:
        rest = body(rest)
    dumped = yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
    path.write_text(f"---\n{dumped}---\n{rest}", encoding="utf-8")


def edit_ticket(org: Org, ticket_id: str, change: Callable[[dict[str, Any]], None]) -> None:
    for path in sorted(org.path("support").glob("*.jsonl")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            ticket = json.loads(line)
            if ticket["id"] == ticket_id:
                change(ticket)
                lines[i] = json.dumps(ticket, ensure_ascii=False)
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return
    raise AssertionError(f"no ticket {ticket_id}")


def remove_tickets(org: Org, ticket_ids: set[str]) -> None:
    for path in sorted(org.path("support").glob("*.jsonl")):
        kept = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if json.loads(line)["id"] not in ticket_ids
        ]
        path.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")


async def document(db: Database, org: Org, external_id: str) -> dict[str, Any]:
    found = await rows(
        db,
        org.id,
        "SELECT id, version, content_hash, access_hash, status, sensitivity, access_mode, "
        "department_ids, account_id, ingested_at, acl_updated_at, body "
        "FROM documents WHERE external_id = :e",
        e=external_id,
    )
    assert len(found) == 1, external_id
    names = [
        "id",
        "version",
        "content_hash",
        "access_hash",
        "status",
        "sensitivity",
        "access_mode",
        "department_ids",
        "account_id",
        "ingested_at",
        "acl_updated_at",
        "body",
    ]
    return dict(zip(names, found[0], strict=True))


async def chunks(db: Database, org: Org, document_id: object) -> list[dict[str, Any]]:
    found = await rows(
        db,
        org.id,
        "SELECT id, ordinal, text, embedding_status, embedding IS NOT NULL, embedding_model, "
        "xmin::text FROM chunks WHERE document_id = :d ORDER BY ordinal",
        d=document_id,
    )
    names = ["id", "ordinal", "text", "status", "has_vector", "model", "xmin"]
    return [dict(zip(names, r, strict=True)) for r in found]


async def ids_of(db: Database, org: Org, table: str, where: str = "true") -> set[uuid.UUID]:
    found = await rows(db, org.id, f"SELECT id FROM {table} WHERE {where}")  # noqa: S608
    return {r[0] for r in found}
