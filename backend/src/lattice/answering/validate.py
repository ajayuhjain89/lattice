"""Claim-level, fail-closed validation of a model draft (SEC-7, SEC-13;
retrieval-design §9).

The unit of validation is the **claim**. A claim survives only if *every* one of its
citations validates; one failed citation rejects the whole claim, which is never shown
with the bad citation stripped. Rules, in order, for each claim:

1. **Shape** -- the claim parses (text, role, citations); 1-5 citations; text 1-600
   characters (``malformed_claim``, ``no_citations``, ``too_many_citations``,
   ``claim_too_long``).
2. **Handles** -- every handle is one of this request's prompt handles (``E1`` ...
   ``En``). The model cannot cite a document ID, another request's handle, or evidence
   it was not given (``unknown_handle``).
3. **Quotes** -- every quote is found verbatim in its handle's chunk text after
   normalization only: Unicode NFC, whitespace collapsed, curly/straight quotes and
   dash variants treated alike; surrounding quotation marks and a trailing ellipsis are
   ignored. No fuzzy matching, no case folding. A quote must be substantial: at least
   two words and six characters, at most 300 characters (``quote_not_found``,
   ``quote_too_short``, ``quote_too_long``). The verified offsets become the
   inspector's highlight.
4. **Support floor** -- deterministic checks that one valid quote cannot legitimize an
   unrelated statement: every number in the claim occurs in the text or metadata of the
   evidence the claim cites (``unsupported_number``), and a claim with content words
   (not counting the cited sources' own titles, authors, references, dates and generic
   attribution words) shares at least one with the sentence around one of its quotes
   (``no_shared_terms``). Whether a quote really *entails* its claim is a language
   judgement, measured by the evaluation judge.
5. **Safe content** -- no URLs, web or email addresses, markdown links/images or HTML
   in anything shown (``unsafe_content``): exfiltration through rendered links is
   impossible even if the model repeats an injected payload.

Conflict positions are claims and follow the same rules; a conflict needs at least two
valid positions citing at least two distinct documents, or it is discarded. Non-claim
text (``unknowns``, ``clarifying_question``, conflict ``topic``) is length-limited and
must be safe; an unsafe item is dropped.

Rejected claims are reported as ``Rejection`` (index, code, handle) -- never their text.
"""

import re
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from pydantic import ValidationError

from lattice.answering.models import AnswerDraft, DraftClaim, DraftConflict, DraftStatus
from lattice.answering.prompt import evidence_date
from lattice.retrieval import Evidence

MAX_CITATIONS: Final = 5
MAX_CLAIM_CHARS: Final = 600
MAX_QUOTE_CHARS: Final = 300
MIN_QUOTE_CHARS: Final = 6
MIN_QUOTE_WORDS: Final = 2
MAX_TOPIC_CHARS: Final = 80
MAX_UNKNOWNS: Final = 5
MAX_UNKNOWN_CHARS: Final = 200
MAX_CLARIFYING_CHARS: Final = 300

# Curly/low/reversed quotes and primes -> straight quotes; hyphen and dash variants and
# the minus sign -> "-". Code points, so the source stays ASCII.
_EQUIVALENT = str.maketrans(
    {cp: "'" for cp in (0x2018, 0x2019, 0x201A, 0x201B, 0x2032)}
    | {cp: '"' for cp in (0x201C, 0x201D, 0x201E, 0x2033)}
    | {cp: "-" for cp in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212)}
)
_OUTER = "\"'`"
_UNSAFE = re.compile(
    r"https?:|ftp:|file:|data:|mailto:|www\.|\]\(|!\[|<\s*[a-z/!]"
    r"|[\w.+-]+@[\w-]+\.[\w.-]+"
    r"|\b(?:[a-z0-9-]+\.)+(?:com|net|org|io|ai|dev|app|co|example|info|biz|xyz|me|us|uk|"
    r"de|cloud|site|online|link|ly)\b",
    re.I,
)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_HANDLE_TOKEN = re.compile(r"\bE\d+\b")
_WORD = re.compile(r"[a-z][a-z'-]*[a-z]|[a-z]")
_STOPWORDS: Final = frozenset(
    {
        "about",
        "above",
        "after",
        "again",
        "against",
        "also",
        "although",
        "among",
        "another",
        "any",
        "are",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "can",
        "cannot",
        "could",
        "did",
        "does",
        "doing",
        "done",
        "down",
        "during",
        "each",
        "either",
        "else",
        "ever",
        "every",
        "few",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "her",
        "here",
        "hers",
        "him",
        "his",
        "how",
        "however",
        "into",
        "its",
        "itself",
        "just",
        "least",
        "less",
        "like",
        "made",
        "make",
        "many",
        "may",
        "might",
        "more",
        "most",
        "much",
        "must",
        "neither",
        "never",
        "nor",
        "not",
        "now",
        "off",
        "once",
        "only",
        "other",
        "ought",
        "our",
        "ours",
        "out",
        "over",
        "own",
        "same",
        "says",
        "said",
        "shall",
        "she",
        "should",
        "since",
        "some",
        "still",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "though",
        "through",
        "thus",
        "too",
        "under",
        "until",
        "upon",
        "very",
        "was",
        "were",
        "what",
        "when",
        "where",
        "whether",
        "which",
        "while",
        "who",
        "whom",
        "whose",
        "why",
        "will",
        "with",
        "within",
        "without",
        "would",
        "yet",
        "you",
        "your",
        "yours",
        "evidence",
        "source",
        "sources",
        "states",
        "stated",
        "according",
        "item",
        "items",
    }
)


_ATTRIBUTION: Final = frozenset(
    w.rstrip("s")
    for w in [
        "january",
        "february",
        "march",
        "april",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "today",
        "yesterday",
        "week",
        "month",
        "year",
        "ticket",
        "tickets",
        "document",
        "documents",
        "doc",
        "docs",
        "message",
        "messages",
        "thread",
        "channel",
        "slack",
        "comment",
        "comments",
        "note",
        "notes",
        "record",
        "records",
        "email",
        "call",
        "meeting",
        "report",
        "reported",
        "wrote",
        "write",
        "writes",
        "mentioned",
        "mentions",
        "told",
        "tells",
        "confirmed",
        "confirms",
        "noted",
        "notes",
        "shared",
        "posted",
    ]
)


def normalize(text: str) -> str:
    """The comparison form of a quote."""
    return " ".join(unicodedata.normalize("NFC", text).translate(_EQUIVALENT).split())


def _normalized_with_offsets(text: str) -> tuple[str, list[int]]:
    """Normalized text and, per normalized character, its offset in ``text``. Chunk text
    is NFC already (ingestion canonicalizes it), so NFC does not move offsets."""
    source = unicodedata.normalize("NFC", text).translate(_EQUIVALENT)
    out: list[str] = []
    offsets: list[int] = []
    pending_space = False
    for index, char in enumerate(source):
        if char.isspace():
            pending_space = bool(out)
            continue
        if pending_space:
            out.append(" ")
            offsets.append(index - 1)
            pending_space = False
        out.append(char)
        offsets.append(index)
    return "".join(out), offsets


def locate(quote: str, text: str) -> tuple[int, int] | None:
    """Offsets of ``quote`` in ``text`` (first occurrence), or None."""
    needle = normalize(quote)
    if not needle:
        return None
    haystack, offsets = _normalized_with_offsets(text)
    at = haystack.find(needle)
    if at < 0:
        return None
    return offsets[at], offsets[at + len(needle) - 1] + 1


def _strip_quote(quote: str) -> str:
    q = quote.strip()
    for ellipsis in ("...", "\u2026"):
        q = q.removeprefix(ellipsis).removesuffix(ellipsis).strip()
    while len(q) >= 2 and q[0] in _OUTER and q[-1] in _OUTER:
        q = q[1:-1].strip()
    return q


_BOUNDARY = re.compile(r"[.!?](?=\s)|\n")


def _sentence(text: str, start: int, end: int) -> str:
    """The sentence(s) around a verified quote: its context for the shared-terms check."""
    before = [m.end() for m in _BOUNDARY.finditer(text, 0, start)]
    after = _BOUNDARY.search(text, end)
    return text[(before[-1] if before else 0) : (after.end() if after else len(text))]


def is_safe(text: str) -> bool:
    return _UNSAFE.search(text) is None


def _numbers(text: str) -> set[str]:
    found = set()
    for raw in _NUMBER.findall(text):
        compact = raw.replace(",", "")
        whole, _, fraction = compact.partition(".")
        whole = whole.lstrip("0") or "0"
        found.add(f"{whole}.{fraction.rstrip('0')}".rstrip(".") if fraction else whole)
    return found


def _terms(text: str) -> set[str]:
    words = _WORD.findall(normalize(text).lower().replace("'s", ""))
    return {w.rstrip("s") for w in words if len(w) >= 4 and w not in _STOPWORDS}


def evidence_facts(e: Evidence) -> str:
    """What the model saw of an item besides its text (prompt attributes)."""
    return " ".join(
        filter(None, [e.title, e.display_ref, evidence_date(e).date().isoformat(), e.author])
    )


# --- results --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VerifiedCitation:
    handle: str
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    quote: str  # the exact substring of the chunk text
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class ValidClaim:
    text: str
    role: str
    citations: tuple[VerifiedCitation, ...]

    @property
    def document_ids(self) -> frozenset[uuid.UUID]:
        return frozenset(c.document_id for c in self.citations)


@dataclass(frozen=True, slots=True)
class ValidConflict:
    topic: str | None
    positions: tuple[ValidClaim, ...]


@dataclass(frozen=True, slots=True)
class Rejection:
    """Where and why -- never the rejected text."""

    location: str  # "claim 3", "conflict 1 position 2"
    code: str
    handle: str | None = None

    def feedback(self) -> str:
        subject = f"{self.location}: "
        messages = {
            "unknown_handle": f"{self.handle} is not one of the evidence handles",
            "quote_not_found": f"the quote is not found verbatim in {self.handle}",
            "quote_too_short": f"the quote from {self.handle} is too short",
            "quote_too_long": f"the quote from {self.handle} is longer than 300 characters",
            "no_citations": "every claim needs at least one citation",
            "too_many_citations": "a claim may cite at most 5 quotes",
            "claim_too_long": "the claim is longer than 600 characters",
            "unsupported_number": "the claim contains a number that its cited evidence "
            "does not contain",
            "no_shared_terms": "the claim shares no content words with its quotes",
            "unsafe_content": "the claim contains a link, address, markdown or HTML",
            "malformed_claim": "the claim does not match the schema",
            "conflict_invalid": "a conflict needs two valid positions from two sources",
        }
        return subject + messages.get(self.code, self.code)


@dataclass
class Validation:
    schema_ok: bool
    model_status: DraftStatus | None = None
    claims: list[ValidClaim] = field(default_factory=list)
    conflicts: list[ValidConflict] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    clarifying_question: str | None = None
    rejections: list[Rejection] = field(default_factory=list)
    claims_proposed: int = 0
    positions_proposed: int = 0
    conflicts_proposed: int = 0
    conflicts_discarded: int = 0

    @property
    def claims_rejected(self) -> int:
        return sum(1 for r in self.rejections if r.location.startswith("claim"))

    def rejection_codes(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.rejections:
            counts[r.code] = counts.get(r.code, 0) + 1
        return dict(sorted(counts.items()))


# --- validation -----------------------------------------------------------------------


def _claim(
    raw: object, location: str, by_handle: Mapping[str, Evidence]
) -> tuple[ValidClaim | None, Rejection | None]:
    try:
        claim = DraftClaim.model_validate(raw)
    except ValidationError:
        return None, Rejection(location, "malformed_claim")
    text = " ".join(claim.text.split())
    if not claim.citations:
        return None, Rejection(location, "no_citations")
    if len(claim.citations) > MAX_CITATIONS:
        return None, Rejection(location, "too_many_citations")
    if not text or len(text) > MAX_CLAIM_CHARS:
        return None, Rejection(location, "claim_too_long" if text else "malformed_claim")
    if not is_safe(text):
        return None, Rejection(location, "unsafe_content")
    verified: list[VerifiedCitation] = []
    for citation in claim.citations:
        handle = citation.handle.strip()
        evidence = by_handle.get(handle)
        if evidence is None:
            return None, Rejection(location, "unknown_handle", handle[:16])
        quote = _strip_quote(citation.quote)
        normalized = normalize(quote)
        if len(normalized) > MAX_QUOTE_CHARS:
            return None, Rejection(location, "quote_too_long", handle)
        if len(normalized) < MIN_QUOTE_CHARS or len(normalized.split()) < MIN_QUOTE_WORDS:
            return None, Rejection(location, "quote_too_short", handle)
        span = locate(quote, evidence.text)
        if span is None:
            return None, Rejection(location, "quote_not_found", handle)
        if not is_safe(evidence.text[span[0] : span[1]]):
            return None, Rejection(location, "unsafe_content", handle)
        verified.append(
            VerifiedCitation(
                handle=handle,
                chunk_id=evidence.chunk_id,
                document_id=evidence.document_id,
                quote=evidence.text[span[0] : span[1]],
                start=span[0],
                end=span[1],
            )
        )
    cited = [by_handle[c.handle] for c in verified]
    allowed = set().union(*(_numbers(f"{e.text} {evidence_facts(e)}") for e in cited))
    if not _numbers(_HANDLE_TOKEN.sub(" ", text)) <= allowed:
        return None, Rejection(location, "unsupported_number")
    # Attribution ("The postmortem of 3 Aug says ...") names the source, not its content:
    # the cited items' own metadata and generic source/date words do not count.
    claim_terms = (
        _terms(_HANDLE_TOKEN.sub(" ", text))
        - set().union(*(_terms(evidence_facts(e)) for e in cited))
        - _ATTRIBUTION
    )
    context = set().union(
        *(_terms(_sentence(by_handle[c.handle].text, c.start, c.end)) for c in verified)
    )
    if claim_terms and not claim_terms & context:
        return None, Rejection(location, "no_shared_terms")
    return ValidClaim(text=text, role=claim.role, citations=tuple(verified)), None


def _safe_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or len(text) > limit or not is_safe(text):
        return None
    return text


def validate_draft(raw: str, by_handle: Mapping[str, Evidence]) -> Validation:
    """Validate one model output against this request's prompt handles."""
    try:
        draft = AnswerDraft.model_validate_json(raw)
    except ValidationError:
        return Validation(schema_ok=False)
    out = Validation(schema_ok=True, model_status=draft.status)
    out.claims_proposed = len(draft.claims)
    for index, raw_claim in enumerate(draft.claims, start=1):
        claim, rejection = _claim(raw_claim, f"claim {index}", by_handle)
        if claim is not None:
            out.claims.append(claim)
        elif rejection is not None:
            out.rejections.append(rejection)
    out.conflicts_proposed = len(draft.conflicts)
    for c_index, raw_conflict in enumerate(draft.conflicts, start=1):
        try:
            conflict = DraftConflict.model_validate(raw_conflict)
        except ValidationError:
            out.rejections.append(Rejection(f"conflict {c_index}", "malformed_claim"))
            out.conflicts_discarded += 1
            continue
        positions: list[ValidClaim] = []
        for p_index, raw_position in enumerate(conflict.positions, start=1):
            out.positions_proposed += 1
            position, rejection = _claim(
                raw_position, f"conflict {c_index} position {p_index}", by_handle
            )
            if position is not None:
                positions.append(position)
            elif rejection is not None:
                out.rejections.append(rejection)
        documents = set().union(*(p.document_ids for p in positions)) if positions else set()
        if len(positions) < 2 or len(documents) < 2:
            out.conflicts_discarded += 1
            out.rejections.append(Rejection(f"conflict {c_index}", "conflict_invalid"))
            continue
        out.conflicts.append(
            ValidConflict(
                topic=_safe_text(conflict.topic, MAX_TOPIC_CHARS), positions=tuple(positions)
            )
        )
    out.unknowns = [
        u for u in (_safe_text(v, MAX_UNKNOWN_CHARS) for v in draft.unknowns) if u is not None
    ][:MAX_UNKNOWNS]
    out.clarifying_question = _safe_text(draft.clarifying_question, MAX_CLARIFYING_CHARS)
    return out
