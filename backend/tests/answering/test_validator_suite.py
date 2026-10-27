"""The validator's adversarial suite (P13.7, EC-4): authored drafts against authored evidence.

Every case is one claim validated on its own against the same five evidence items, as a
scripted provider would send it. Three kinds:

* ``invalid`` -- a claim the validator's contract must reject (retrieval-design §9):
  foreign or unknown handles, quotes that are not verbatim in the cited item (paraphrased,
  altered, borrowed from another item), numbers its cited evidence does not contain,
  quotes unrelated to the claim, unsafe content, malformed shapes. Acceptance: **all**
  rejected, each with the expected code.
* ``valid`` -- claims a careful model would write: paraphrase around a verbatim quote,
  attribution, several citations, numbers and dates from the text or the item's metadata.
  Acceptance: at most 5% rejected (phase-13-plan §11 P13.7).
* ``beyond`` -- wrong statements that share words with a real quote and carry no number:
  whether a quote *entails* its claim is a language judgement the deterministic validator
  does not make (the evaluation judge, D-44). They pass today; listed so the boundary is
  explicit and a change to it is noticed.

The suite's numbers are recorded in evaluation-plan §21.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import pytest

from lattice.answering.validate import Validation, validate_draft

from .helpers import claim, draft
from .test_validate_prompt import ev

DECISION = ev(
    "Decision (18 September 2026): Directory Sync general availability moves from "
    "15 October to 8 December 2026. Acme Corp joins the beta on 3 November. Customers "
    "must not be promised a date before GA.",
    "Directory Sync GA decision",
    when=datetime(2026, 9, 18, tzinfo=UTC),
)
TICKET = ev(
    "Support replied to Derek Holt: SCIM provisioning is expected in October; we will "
    "confirm the exact date next week.",
    "HAL-4821 SCIM provisioning",
    rank=2,
    ref="HAL-4821",
    when=datetime(2026, 9, 2, tzinfo=UTC),
)
POSTMORTEM = ev(
    "INC-2026-031: the dashboard outage lasted 47 minutes on 9 September. Root cause: an "
    "expired TLS certificate on the metrics proxy. Action: automate renewal by 30 September.",
    "Postmortem INC-2026-031",
    rank=3,
)
POLICY = ev(
    "Enterprise customers may request a full refund within 14 days of purchase. After "
    "14 days, refunds are prorated to the unused months.",
    "Refund policy",
    rank=4,
)
CHAT = ev(
    "Grace: the Kestrel renewal is at risk; they want a 12% discount and the SSO "
    "timeouts fixed before signing.",
    "#acct-kestrel",
    rank=5,
    when=datetime(2026, 9, 21, tzinfo=UTC),
)
HANDLES = {"E1": DECISION, "E2": TICKET, "E3": POSTMORTEM, "E4": POLICY, "E5": CHAT}

GA = ("E1", "general availability moves from 15 October to 8 December 2026")
BETA = ("E1", "Acme Corp joins the beta on 3 November")
NO_PROMISE = ("E1", "Customers must not be promised a date before GA")
EXPECTED = ("E2", "SCIM provisioning is expected in October")
CONFIRM = ("E2", "we will confirm the exact date next week")
OUTAGE = ("E3", "the dashboard outage lasted 47 minutes on 9 September")
CAUSE = ("E3", "Root cause: an expired TLS certificate on the metrics proxy")
ACTION = ("E3", "automate renewal by 30 September")
REFUND = ("E4", "may request a full refund within 14 days of purchase")
PRORATED = ("E4", "refunds are prorated to the unused months")
RISK = ("E5", "the Kestrel renewal is at risk")
ASK = ("E5", "they want a 12% discount and the SSO timeouts fixed before signing")

Kind = Literal["valid", "invalid", "beyond"]


@dataclass(frozen=True)
class Case:
    id: str
    kind: Kind
    claim: dict[str, Any]
    code: str | None = None  # the expected rejection code of an invalid case


def _valid(id: str, text: str, *citations: tuple[str, str], role: str = "direct_answer") -> Case:
    return Case(id, "valid", claim(text, *citations, role=role))


def _invalid(id: str, code: str, text: str, *citations: tuple[str, str]) -> Case:
    return Case(id, "invalid", claim(text, *citations), code)


def _beyond(id: str, text: str, *citations: tuple[str, str]) -> Case:
    return Case(id, "beyond", claim(text, *citations))


CASES: tuple[Case, ...] = (
    # --- valid: what a careful model writes ------------------------------------------
    _valid("v-ga-date", "Directory Sync GA moved to 8 December 2026.", GA),
    _valid("v-ga-from-to", "GA was moved from 15 October to 8 December 2026.", GA),
    _valid("v-beta", "Acme Corp joins the beta on 3 November.", BETA),
    _valid("v-no-promise", "No date before GA may be promised to customers.", NO_PROMISE),
    _valid(
        "v-attribution",
        "The GA decision of 18 September moved general availability to 8 December 2026.",
        GA,
    ),
    _valid("v-meta-date", "On 18 September the beta start for Acme Corp was set.", BETA),
    _valid("v-ticket", "Support told Derek Holt that SCIM was expected in October.", EXPECTED),
    _valid("v-ticket-ref", "HAL-4821 says SCIM provisioning was expected in October.", EXPECTED),
    _valid("v-confirm", "Support promised to confirm the exact date the next week.", CONFIRM),
    _valid("v-outage", "The dashboard outage lasted 47 minutes.", OUTAGE),
    _valid("v-outage-day", "The outage happened on 9 September and lasted 47 minutes.", OUTAGE),
    _valid("v-cause", "An expired TLS certificate on the metrics proxy caused it.", CAUSE),
    _valid("v-action", "Certificate renewal is to be automated by 30 September.", ACTION),
    _valid("v-incident-id", "INC-2026-031 was caused by an expired certificate.", CAUSE),
    _valid("v-refund", "Enterprise customers can get a full refund within 14 days.", REFUND),
    _valid("v-prorated", "After the first 14 days refunds are prorated.", PRORATED, REFUND),
    _valid("v-risk", "The Kestrel renewal is at risk.", RISK),
    _valid("v-discount", "Kestrel wants a 12% discount before signing.", ASK),
    _valid("v-sso", "Kestrel wants the SSO timeouts fixed before it signs.", ASK),
    _valid("v-grace", "Grace said the Kestrel renewal was at risk.", RISK),
    _valid("v-two-items", "SCIM was expected in October but GA moved to December.", EXPECTED, GA),
    _valid(
        "v-three-items",
        "GA moved to 8 December 2026; Acme Corp joins the beta on 3 November and no "
        "earlier date may be promised.",
        GA,
        BETA,
        NO_PROMISE,
    ),
    _valid(
        "v-context",
        "Supporting context: the outage was caused by an expired certificate.",
        CAUSE,
        role="supporting",
    ),
    _valid("v-curly-quote", "SCIM was “expected in October”.", ("E2", "“expected in October”")),
    _valid("v-dash", "The incident INC\u20112026\u2011031 lasted 47 minutes.", OUTAGE),
    _valid(
        "v-ellipsis",
        "Refunds are prorated after 14 days.",
        ("E4", "After 14 days, refunds are prorated…"),
    ),
    _valid("v-handle-in-text", "E3 shows the outage lasted 47 minutes.", OUTAGE),
    _valid("v-percent", "The discount Kestrel asked for is 12%.", ASK),
    _valid("v-year", "General availability is now planned for December 2026.", GA),
    _valid("v-unused", "Refunds after the window cover only the unused months.", PRORATED),
    # --- invalid: handles ---------------------------------------------------------------
    _invalid("i-handle-missing", "unknown_handle", "GA moved to 8 December 2026.", ("E9", GA[1])),
    _invalid("i-handle-other-request", "unknown_handle", "GA moved.", ("E12", GA[1])),
    _invalid(
        "i-handle-document-id",  # longer than a handle may be: the schema refuses it
        "malformed_claim",
        "GA moved to 8 December 2026.",
        (str(DECISION.document_id), GA[1]),
    ),
    _invalid("i-handle-lowercase", "unknown_handle", "GA moved to 8 December 2026.", ("e1", GA[1])),
    _invalid("i-handle-chunk", "malformed_claim", "GA moved.", (str(DECISION.chunk_id), GA[1])),
    # --- invalid: quotes not verbatim in the cited item -----------------------------------
    _invalid(
        "i-quote-paraphrased",
        "quote_not_found",
        "GA moved to 8 December 2026.",
        ("E1", "GA was moved to 8 December 2026"),
    ),
    _invalid(
        "i-quote-number-altered",
        "quote_not_found",
        "The outage lasted 74 minutes.",
        ("E3", "the dashboard outage lasted 74 minutes"),
    ),
    _invalid(
        "i-quote-other-item",  # a real quote, attributed to the wrong item
        "quote_not_found",
        "The outage lasted 47 minutes.",
        ("E1", OUTAGE[1]),
    ),
    _invalid(
        "i-quote-spliced",
        "quote_not_found",
        "GA moved to 8 December and Acme joins the beta.",
        ("E1", "moves from 15 October to 8 December 2026 and Acme Corp joins the beta"),
    ),
    _invalid(
        "i-quote-case",
        "quote_not_found",
        "Acme Corp joins the beta on 3 November.",
        ("E1", "acme corp joins the beta on 3 november"),
    ),
    _invalid(
        "i-quote-invented",
        "quote_not_found",
        "Acme was promised SCIM by October.",
        ("E2", "we promised Acme SCIM by October"),
    ),
    _invalid("i-quote-one-word", "quote_too_short", "The renewal is at risk.", ("E5", "risk")),
    _invalid("i-quote-long", "quote_too_long", "Refunds.", ("E4", "refund " * 60)),
    # --- invalid: numbers the cited evidence does not contain -----------------------------
    _invalid("i-number-minutes", "unsupported_number", "The outage lasted 74 minutes.", OUTAGE),
    _invalid("i-number-days", "unsupported_number", "Refunds are possible within 30 days.", REFUND),
    _invalid("i-number-year", "unsupported_number", "GA moved to 8 December 2027.", GA),
    _invalid("i-number-day", "unsupported_number", "Acme joins the beta on 13 November.", BETA),
    _invalid("i-number-percent", "unsupported_number", "Kestrel wants a 20% discount.", ASK),
    _invalid(
        "i-number-other-citation",  # 47 is in E3, not in the evidence this claim cites
        "unsupported_number",
        "The GA move followed a 47 minute outage.",
        GA,
    ),
    _invalid("i-number-invented", "unsupported_number", "Kestrel has 250 seats at risk.", RISK),
    # --- invalid: a real quote that does not concern the claim ----------------------------
    _invalid("i-unrelated", "no_shared_terms", "Acme's NPS score is excellent.", REFUND),
    _invalid(
        "i-cross-claim",  # the quote supports a different claim of the same answer
        "no_shared_terms",
        "Kestrel's SSO problems are solved.",
        CAUSE,
    ),
    _invalid("i-unrelated-person", "no_shared_terms", "Priya approved the budget.", RISK),
    _invalid(
        "i-unrelated-attribution", "no_shared_terms", "The ticket says pricing increases.", EXPECTED
    ),
    # --- invalid: unsafe content and shape -------------------------------------------------
    _invalid("i-url", "unsafe_content", "See https://evil.example for the GA date.", GA),
    _invalid("i-markdown", "unsafe_content", "GA moved, [details](x) here.", GA),
    _invalid("i-email", "unsafe_content", "Write to derek@acme.example about GA.", GA),
    _invalid("i-html", "unsafe_content", "GA moved <img src=x> to December.", GA),
    _invalid("i-no-citation", "no_citations", "GA moved to 8 December 2026."),
    _invalid("i-too-many", "too_many_citations", "Many sources agree.", *([GA] * 6)),
    _invalid("i-too-long", "claim_too_long", "GA " * 300, GA),
    # --- beyond the deterministic contract (pass today; judge territory) ------------------
    _beyond("b-negated", "Directory Sync general availability does not move.", GA),
    _beyond("b-month-swapped", "Acme Corp joins the beta in October.", BETA),
    _beyond("b-wrong-person", "Grace wants the SSO timeouts fixed before signing.", ASK),
    _beyond("b-overclaim", "Customers were promised a date before GA.", NO_PROMISE),
)


def _validate(case: Case) -> Validation:
    return validate_draft(json.dumps(draft(case.claim)), HANDLES)


def _rejected(case: Case) -> str | None:
    v = _validate(case)
    if v.claims:
        return None
    (rejection,) = v.rejections
    return rejection.code


@pytest.mark.parametrize("case", [c for c in CASES if c.kind == "invalid"], ids=lambda c: c.id)
def test_every_invalid_claim_is_rejected_with_its_code(case: Case) -> None:
    assert _rejected(case) == case.code


def test_valid_claims_are_rejected_at_most_five_percent() -> None:
    valid = [c for c in CASES if c.kind == "valid"]
    rejected = {c.id: _rejected(c) for c in valid if _rejected(c) is not None}
    assert len(rejected) <= 0.05 * len(valid), rejected


def test_the_suite_is_large_enough_to_mean_something() -> None:
    kinds = [c.kind for c in CASES]
    assert kinds.count("valid") >= 30
    assert kinds.count("invalid") >= 30
    assert len({c.id for c in CASES}) == len(CASES)
    codes = {c.code for c in CASES if c.kind == "invalid"}
    assert {"unknown_handle", "quote_not_found", "unsupported_number", "no_shared_terms"} <= codes


@pytest.mark.parametrize("case", [c for c in CASES if c.kind == "beyond"], ids=lambda c: c.id)
def test_entailment_is_beyond_the_deterministic_validator(case: Case) -> None:
    # Documented boundary: these wrong claims pass. Strengthening the validator to catch
    # one moves its case to ``invalid``; never relax a check to keep one passing.
    assert _rejected(case) is None
