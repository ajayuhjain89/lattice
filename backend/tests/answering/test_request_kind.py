"""D-11 by code: action requests are recognised from the question alone, and questions
that merely start with a verb-like noun are not refused."""

import pytest

from lattice.answering.request_kind import is_action_request


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
    ],
)
def test_questions(question: str) -> None:
    assert not is_action_request(question)
