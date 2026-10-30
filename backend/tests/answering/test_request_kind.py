"""D-11 by code: action requests are recognised from the question alone, and questions
that merely start with a verb-like noun are not refused.

The record and calendar verbs (O12, phase-13-followups §14) come from D-11's definition --
acting on a calendar, ticket or record: book, open, file, set up, add, invite, post,
submit, ... -- not from the wording of the golden items that found the gap. Every dev
question is classified exactly as labelled."""

from pathlib import Path

import pytest

from lattice.answering.request_kind import is_action_request
from lattice.dataset.lint import load_dataset

REPO = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "question",
    [
        "Draft an email to Acme promising SCIM in October.",
        "Give me access to the executive escalation notes for Acme Corp.",
        "Show me the questions Maya asked Lattice this week.",
        "Update HAL-4821 to tell Derek that GA is 8 December.",
        "Please send Derek the GA date.",
        "Can you update HAL-4821 to urgent?",
        "Could you please close HAL-4821?",
        "I want you to email the customer.",
        "Hey Lattice, reply to Derek.",
        "Tell Derek that GA is 8 December.",
        "Let Derek know GA moved.",
        "Revoke Maya's access to the Kestrel tickets.",
        "What did Maya ask Lattice yesterday?",
        # O12: acting on a calendar, ticket or record.
        "Book a meeting with Derek next week.",
        "Can you book the Orbital room for Thursday?",
        "Open a ticket for the SSO outage.",
        "Please open a new incident for the Kestrel sync failure.",
        "File a bug against Directory Sync.",
        "Raise a P1 for the billing export.",
        "Log a support case for Tidewell.",
        "Submit the renewal quote to Acme.",
        "Post a summary in the support channel.",
        "Add Maya to HAL-4821.",
        "Add a comment to HAL-4877 saying the fix shipped.",
        "Invite Derek to the SCIM review.",
        "Set up a call with the Acme admins.",
        "Could you set HAL-4821 to urgent?",
        "Arrange a sync with Kestrel's IT team.",
        "Mark HAL-4821 as resolved.",
        "Move the Kestrel renewal to November.",
        "Share the refund policy with Tidewell.",
        "Link HAL-4533 to the refund policy.",
        "Remind me to chase Acme on Friday.",
        "Remind Derek about the GA date.",
        "Ping Grace about the invoice.",
        "Issue a refund to Quarry.",
    ],
)
def test_action_requests(question: str) -> None:
    assert is_action_request(question)


@pytest.mark.parametrize(
    "question",
    [
        "What is Acme Corp's NPS score?",
        "Summarize ticket HAL-4877.",
        "What access changes were made for the Lattice pilot in September?",
        "Which customers did Support tell that SCIM would arrive in October?",
        "Update on Acme?",
        "Update me on the Kestrel renewal.",
        "Schedule for Directory Sync GA?",
        "Change log for refund policy v2?",
        "Close-out plan for the Kestrel outage?",
        "Reply from Acme about SCIM?",
        "Could you tell me the GA date?",
        "Tell me about Project Tern.",
        # O12: nouns and adjectives that look like the record and calendar verbs.
        "Open issues for Acme?",
        "Open the risks list: what is still open for Kestrel?",
        "Book of business for the enterprise segment?",
        "File retention rules for EU customers?",
        "Log of SSO failures last week?",
        "Post-incident review for the Kestrel outage?",
        "Add-on pricing for Enterprise Plus?",
        "Invite list for the customer advisory board?",
        "Set of customers on the legacy refund policy?",
        "Mark Chen's renewal: what was agreed?",
        "Move to the new SLA: when did it take effect?",
        "Share of tickets escalated in September?",
        "Link to the SCIM RFC?",
        "Remind me what Acme's renewal date is.",
        "Call notes from the Acme kickoff?",
        "Message from Derek about SCIM?",
        "Issue with SSO for Kestrel?",
        "Who should book the Orbital review?",
        "When was HAL-4821 opened?",
        "Did anyone file a ticket about the certificate rotation?",
        "Share the latest on the Acme renewal.",
        "Share the refund policy with me.",
        "Flag anything risky in the Kestrel renewal.",
    ],
)
def test_questions(question: str) -> None:
    assert not is_action_request(question)


def test_every_dev_question_is_classified_as_labelled() -> None:
    """No false positive and no miss over the dev split (held-out and sealed items are
    not used to shape the patterns; the golden security test confirms them)."""
    dataset = load_dataset(REPO / "data" / "synthetic", REPO / "evals" / "golden")
    dev = [i for _, i in dataset.goldens if i.split == "dev"]
    assert len(dev) > 100
    wrong = [
        (i.id, i.question)
        for i in dev
        if is_action_request(i.question)
        != any(e.status == "unsupported_request" for _, e in i.expectations())
    ]
    assert wrong == []
