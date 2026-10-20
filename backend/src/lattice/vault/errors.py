"""The one failure type of every secret reference (``env:``, ``memory:``, ``vault:``)."""


class SecretUnavailable(Exception):
    """A reference could not be resolved. ``code`` is fixed: ``invalid_reference``,
    ``scheme_not_allowed``, ``missing``, ``empty``, ``destroyed``, ``key_unavailable``,
    ``decrypt_failed``, ``no_active_key``. Never the value or the name."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
