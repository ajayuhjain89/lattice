"""Whether a question asks Lattice to act rather than to answer (D-11).

Decided by code, from the question alone, before retrieval's outcome is consulted: an
action request is ``unsupported_request`` / ``action_request`` whether or not any
relevant evidence exists and whether or not a model is available, and no model is
called for it. The patterns are deliberately narrow -- an imperative that performs
something (draft or send a message, change a record, grant access) or a request to read
other people's use of Lattice -- because a false positive refuses a real question. The
model's rubric (``answering.prompt``) still applies to phrasings not listed here when a
model is called.
"""

import re
from typing import Final

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
_ACTION: Final = re.compile(_LEAD + _VERB, re.IGNORECASE)
# Reading what other people asked or did in Lattice is not a question about sources.
_OTHERS_ACTIVITY: Final = re.compile(
    r"\bshow\s+me\s+(?:the\s+|all\s+)?(?:questions|queries|searches|search\s+history)\b"
    r"|\bask(?:ed|s)?\s+lattice\b",
    re.IGNORECASE,
)


def is_action_request(question: str) -> bool:
    return bool(_ACTION.search(question) or _OTHERS_ACTIVITY.search(question))
