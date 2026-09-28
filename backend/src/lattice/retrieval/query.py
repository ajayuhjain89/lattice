"""Deterministic query analysis (retrieval-design §2). No model call, no rewriting, no
expansion: what the reader typed is what is searched.

* ``normalize`` -- Unicode NFC, control and invisible format characters removed,
  whitespace collapsed. Case, punctuation and hyphenation are kept, so ``HAL-4821``,
  ``RFC-2026-17``, ``SCIM``, ``SLA`` and ``Q3`` reach the exact and lexical paths as
  typed (the text-search parser decides tokenization, the same one ingestion used).
* ``extract_identifiers`` -- tokens shaped like a human record reference
  (``PREFIX-123``, ``PREFIX-2026-17``), in order of appearance, at most a few. They are
  looked up exactly against ``documents.display_ref``; a token that names nothing
  matches nothing, so a false positive costs one indexed lookup and changes no result.
* ``has_temporal_intent`` -- words that ask for the current state ("latest", "still",
  "now"). Turns on the recency feature of rerank_v1; never a filter.
* ``mentioned_accounts`` -- account names and aliases found in the question, matched
  **only against the accounts the caller passes**, which must be the reader's visible
  accounts (``authz.visible_accounts_predicate``). A hidden account named in the
  question is plain text: no interpretation, no boost, no ambiguity choice
  (security-model §10, D-10).
"""

import re
import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

# Unicode categories removed from the query: controls (replaced by a space), format
# characters such as zero-width joiners, surrogates, private use and unassigned.
_DROPPED: Final = frozenset({"Cf", "Cs", "Co", "Cn"})
_WHITESPACE = re.compile(r"\s+")

# A letter, 1-15 letters or digits, then one or more "-<digits>" groups; not glued to
# other word characters or hyphens on either side.
_IDENTIFIER = re.compile(r"(?<![\w-])([A-Za-z][A-Za-z0-9]{1,15}(?:-[0-9]{1,12})+)(?![\w-])")

_TEMPORAL = re.compile(
    r"\b(latest|current|currently|now|still|recent|recently|newest|today|"
    r"this (?:week|month)|last (?:week|month))\b",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    """The query as searched. Empty if nothing searchable remains."""
    kept: list[str] = []
    for ch in unicodedata.normalize("NFC", text):
        category = unicodedata.category(ch)
        if category == "Cc":
            kept.append(" ")
        elif category not in _DROPPED:
            kept.append(ch)
    return _WHITESPACE.sub(" ", "".join(kept)).strip()


def extract_identifiers(text: str, limit: int) -> tuple[str, ...]:
    """Record references in order of first appearance, upper-cased (display references
    are stored upper-case), at most ``limit``."""
    found: dict[str, None] = {}
    for match in _IDENTIFIER.finditer(text):
        found.setdefault(match.group(1).upper())
        if len(found) == limit:
            break
    return tuple(found)


def has_temporal_intent(text: str) -> bool:
    return _TEMPORAL.search(text) is not None


@dataclass(frozen=True, slots=True)
class AccountNames:
    """A visible account and the names it may be mentioned by."""

    id: uuid.UUID
    key: str
    name: str
    aliases: tuple[str, ...]

    def labels(self) -> Sequence[str]:
        return (self.name, *self.aliases)


def _mentions(text: str, label: str) -> bool:
    label = label.strip()
    if not label:
        return False
    pattern = r"(?<!\w)" + re.escape(label.casefold()) + r"(?!\w)"
    return re.search(pattern, text.casefold()) is not None


def mentioned_accounts(text: str, visible: Iterable[AccountNames]) -> tuple[AccountNames, ...]:
    """Visible accounts whose name or an alias appears as a whole phrase in ``text``,
    ordered by key. Two or more means the mention is ambiguous (Phase 6 decides what to
    ask); the choices can only ever be accounts the reader may see."""
    return tuple(
        sorted(
            (a for a in visible if any(_mentions(text, label) for label in a.labels())),
            key=lambda a: a.key,
        )
    )
