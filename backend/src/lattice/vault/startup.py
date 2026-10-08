"""The API's start-up check of its master keys against stored ciphertext (C3.3)."""

import logging

from lattice.db.engine import Database
from lattice.log import log_event
from lattice.vault.keys import Keyring
from lattice.vault.records import stored_key_ids

logger = logging.getLogger(__name__)


class SecretKeysMissing(Exception):
    """Stored ciphertext needs a key this process does not hold (IDs only)."""


async def verify_secret_keys(db: Database, keyring: Keyring, *, production: bool) -> None:
    """Every key ID used by live ciphertext must be configured. Production refuses to
    start without one (a restore without its key, a key dropped too early); elsewhere
    it is a warning and the affected credentials fail closed when used."""
    async with db.unscoped_transaction() as session:
        missing = sorted(await stored_key_ids(session) - keyring.ids)
    if not missing:
        return
    message = (
        f"stored credentials are encrypted with key ID(s) {', '.join(missing)} that "
        "LATTICE_SECRET_KEYS does not list"
    )
    if production:
        raise SecretKeysMissing(f"Refusing to start: {message}")
    log_event(logger, logging.WARNING, "secret.keys_missing", key_ids=",".join(missing))
