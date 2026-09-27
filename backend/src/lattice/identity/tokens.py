"""Session tokens. The raw token only ever exists in the cookie; the database stores
its SHA-256. A 256-bit random token needs no slow hash: it cannot be guessed."""

import hashlib
import re
import secrets

TOKEN_BYTES = 32
# token_urlsafe(32) yields 43 URL-safe base64 characters.
_TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def new_session_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_session_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


def is_well_formed(token: str) -> bool:
    return bool(_TOKEN_SHAPE.fullmatch(token))
