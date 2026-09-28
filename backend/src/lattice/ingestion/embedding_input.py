"""Exactly what an embedding provider receives for a chunk, and what the lexical index
weights as its header (docs/ingestion-design.md §3, security-model §6).

Embedding input = a short contextual header + the chunk's own text::

    Support ticket: SSO redirect loop after switching IdP to Entra
    Comment 3 · Priya Natarajan (agent)

    <chunk text>

The header carries only the document's kind label, its title and the chunk's heading
path -- content of the document itself. It never carries (decision D-10, SEC-10):

* structured account names or aliases (a document may be readable through a grant
  while its account is outside the reader's scope, D-8; the header is shared by all
  readers, so an account term here would let a query naming that account match it);
* internal IDs, organization identifiers, sensitivity, access mode, departments,
  groups or grants; authors' emails; the ticket's display reference or any other
  metadata not needed for meaning.

If a document's own text names its customer, that text is content and stays in the
chunk: D-10 forbids *adding* hidden structured metadata, not redacting readable text.

``EMBEDDING_INPUT_VERSION`` is part of ``embedding_input_hash``: changing the template
makes every stored embedding stale by construction (re-embedded by ``lattice reindex
--reembed``, never silently reused).
"""

import hashlib

from lattice.connectors.contract import DocumentKind

EMBEDDING_INPUT_VERSION = "embed-input-1"

KIND_LABELS: dict[DocumentKind, str] = {
    "doc": "Document",
    "ticket": "Support ticket",
    "chat_thread": "Slack thread",
    "chat_channel_day": "Slack channel",
}


def header(kind: DocumentKind, title: str, heading_path: tuple[str, ...]) -> str:
    lines = [f"{KIND_LABELS[kind]}: {title}"]
    if heading_path:
        lines.append(" / ".join(heading_path))
    return "\n".join(lines)


def embedding_input(
    kind: DocumentKind, title: str, heading_path: tuple[str, ...], text: str
) -> str:
    return f"{header(kind, title, heading_path)}\n\n{text}"


def input_hash(text: str) -> bytes:
    return hashlib.sha256(f"{EMBEDDING_INPUT_VERSION}\n{text}".encode()).digest()


def lexical_header(title: str, heading_path: tuple[str, ...]) -> str:
    """Terms weighted 'A' in the chunk's tsvector: title and heading path only (D-10)."""
    return " ".join((title, *heading_path))
