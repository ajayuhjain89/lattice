"""SCIM bearer tokens. The raw token is shown once, to the operator who creates it;
the database stores its SHA-256 (a 256-bit random token needs no slow hash)."""

import hashlib
import re
import secrets
from typing import Final

PREFIX: Final = "lsc_"
_SHAPE = re.compile(r"^lsc_[A-Za-z0-9_-]{43}$")


def new_token() -> str:
    return PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


def is_well_formed(token: str) -> bool:
    return bool(_SHAPE.fullmatch(token))
