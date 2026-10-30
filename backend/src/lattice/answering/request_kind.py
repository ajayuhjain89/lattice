"""Whether a question asks Lattice to act rather than to answer (D-11).

Decided by code, from the question alone, before retrieval's outcome is consulted: an
action request is ``unsupported_request`` / ``action_request`` whether or not any
relevant evidence exists and whether or not a model is available, and no model is
called for it. The patterns are deliberately narrow -- an imperative that performs
something (draft or send a message, change a record, grant access, book or open
something on a calendar or ticket system) or a request to read other people's use of
Lattice -- because a false positive refuses a real question. The model's rubric
(``answering.prompt``) still applies to phrasings not listed here when a model is called.
"""

import re
from typing import Final

# Recorded by the answer evaluation, so a pattern change is a declared change for ``eval
# compare``. 1: Phase 6. 2: O12, the record and calendar verbs.
REQUEST_KIND_VERSION: Final = "request-kind-2"

# Leading politeness that does not change what is asked.
_LEAD: Final = (
    r"^\s*(?:(?:hi|hey|ok|okay)\s+lattice[\s,:;]*)?"
    r"(?:please\s+|kindly\s+)?"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?"
    r"|i\s+(?:want|need|would\s+like)\s+you\s+to\s+"
    r"|go\s+ahead\s+and\s+)?"
    r"(?:please\s+)?"
)
_VERB: Final = (
    r"(?:(?:draft|write|compose|send|e-?mail|reply|respond|forward|update|change|edit|"
    r"modify|delete|remove|create|schedule|assign|reassign|close|reopen|resolve|"
    r"escalate|approve|reject|cancel|grant|revoke|notify)\b"
    # "Schedule for ...?", "Update on ...?", "Change log?", "Close-out plan?": nouns.
    r"(?!-|\s+(?:of|for|from|on|about|in|re|me|us|log|history|status|window)\b)"
    r"|give\s+(?:me|us|\w+)\s+access\b"
    r"|tell\s+(?!me\b|us\b)\w+\s+(?:that|to|about)\b"
    r"|let\s+\w+\s+know\b)"
)
# O12: acting on a calendar, ticket or record (D-11's own list: book, open, file, set up,
# add, invite, post, submit, ...). These words are also nouns and adjectives ("Open
# issues?", "Book of business?", "Mark Chen's renewal?"), so each counts only with the
# object an imperative takes: an article, a pronoun or a ticket key -- and "open" only
# with one that creates ("Open a ticket"), never "Open the ...".
_OBJECT: Final = (
    r"(?:a|an|the|this|that|these|those|another|new|my|our|your|his|her|their|it|them|"
    r"him|me|us|[a-z]{2,10}-\d+)\b"
)
_RECORD_VERB: Final = (
    r"(?:open\s+(?:a|an|another|new)\b"
    r"|(?:book|file|raise|log|submit|post|add|set(?:\s+up)?|arrange|mark|move|link|"
    r"attach|archive|merge|issue)\s+"
    + _OBJECT
    # "Share the latest on Acme" asks to be told; sharing with someone else is an act.
    + r"|share\s+"
    + _OBJECT
    + r"[^?.!]*?\swith\s+(?!me\b|us\b)\w"
    # A person: "Add Maya to ...", "Invite Derek to ...", "Ping Grace about ...".
    + r"|(?:add|invite|ping|loop\s+in|cc)\s+\w+\s+(?:to|into|on|in|about)\b"
    + r"|invite\s+"
    + _OBJECT
    # "Remind me to ..." asks for a reminder; "Remind me what ..." asks a question.
    + r"|remind\s+(?:me|us)\s+to\b|remind\s+(?!me\b|us\b)\w+\s+(?:to|about|that)\b)"
)
_ACTION: Final = re.compile(_LEAD + r"(?:" + _VERB + r"|" + _RECORD_VERB + r")", re.IGNORECASE)
# Reading what other people asked or did in Lattice is not a question about sources.
_OTHERS_ACTIVITY: Final = re.compile(
    r"\bshow\s+me\s+(?:the\s+|all\s+)?(?:questions|queries|searches|search\s+history)\b"
    r"|\bask(?:ed|s)?\s+lattice\b",
    re.IGNORECASE,
)


def is_action_request(question: str) -> bool:
    return bool(_ACTION.search(question) or _OTHERS_ACTIVITY.search(question))
