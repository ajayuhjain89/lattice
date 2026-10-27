import pytest


@pytest.fixture(autouse=True)
def clean_tables() -> None:
    """Overrides the root fixture: these tests compare JSON documents, no database."""
