"""Structured logs never carry secrets or session values (SEC-8)."""

import io
import json
import logging

import pytest
from fastapi import FastAPI

from lattice.db.engine import Database
from lattice.log import REDACTED, JsonFormatter, log_event

from .conftest import CSRF, AppFactory
from .factories import create_member, create_org, login


def _format(record_logger: str, include_exception_text: bool, emit: object) -> dict[str, object]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(include_exception_text=include_exception_text))
    logger = logging.getLogger(record_logger)
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    try:
        assert callable(emit)
        emit(logger)
    finally:
        logger.handlers = []
    return dict(json.loads(stream.getvalue().strip().splitlines()[-1]))


def test_sensitive_field_names_are_redacted() -> None:
    record = _format(
        "lattice.test.fields",
        False,
        lambda log: log_event(
            log,
            logging.INFO,
            "probe",
            session_token="tok-123",
            cookie="c",
            authorization="Bearer abc",
            password="pw",
            database_url="postgresql://u:p@h/db",
            nested={"api_key": "k", "count": 3},
            session_id="11111111-1111-1111-1111-111111111111",
        ),
    )
    fields = record["fields"]
    assert isinstance(fields, dict)
    for key in ("session_token", "cookie", "authorization", "password", "database_url"):
        assert fields[key] == REDACTED
    assert fields["nested"] == {"api_key": REDACTED, "count": 3}
    # Row IDs are not secrets and stay useful for debugging.
    assert fields["session_id"] == "11111111-1111-1111-1111-111111111111"


@pytest.mark.parametrize(
    ("message", "secret"),
    [
        ("connecting to postgresql+asyncpg://lattice_app:hunter2@db/lattice", "hunter2"),
        ("header Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.x.y", "eyJhbGciOiJIUzI1NiJ9"),
        ("Cookie: __Host-lattice_session=AbCdEf0123456789", "AbCdEf0123456789"),
    ],
)
def test_credentials_inside_messages_are_scrubbed(message: str, secret: str) -> None:
    record = _format("lattice.test.scrub", False, lambda log: log.warning(message))
    assert secret not in json.dumps(record)


def test_exception_text_is_omitted_outside_development() -> None:
    def emit(log: logging.Logger) -> None:
        try:
            raise ValueError("SQL: SELECT * FROM users WHERE token = 'secret-value'")
        except ValueError:
            log.exception("failed")

    record = _format("lattice.test.exc", False, emit)
    exception = record["exception"]
    assert isinstance(exception, dict)
    assert exception["type"] == "builtins.ValueError"
    assert exception["frames"]
    assert "secret-value" not in json.dumps(record)


def test_development_includes_scrubbed_exception_text() -> None:
    def emit(log: logging.Logger) -> None:
        try:
            raise ValueError("cannot reach postgresql://u:hunter2@db")
        except ValueError:
            log.exception("failed")

    record = _format("lattice.test.devexc", True, emit)
    text = json.dumps(record)
    assert "cannot reach" in text
    assert "hunter2" not in text


async def test_request_lifecycle_logs_contain_no_session_values(
    app_client: AppFactory, owner_db: Database
) -> None:
    org = await create_org(owner_db, "halden")
    member = await create_member(owner_db, org, "maya@halden.example")
    stream = io.StringIO()

    def capture(app: FastAPI) -> None:
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter(include_exception_text=True))
        logging.getLogger().addHandler(handler)

    async with app_client(configure=capture) as client:
        await login(client, member.email)
        token = client.cookies.get("lattice_session")
        assert token
        await client.get("/v1/me")
        await client.get("/v1/me", headers={"Authorization": "Bearer should-not-appear"})
        await client.post("/v1/auth/logout", headers=CSRF)
        await client.get("/v1/me", headers={"Cookie": f"lattice_session={token}"})
        await client.post("/v1/auth/dev-login", json={"subject": "attacker@x.example"})

    output = stream.getvalue()
    lines = [json.loads(line) for line in output.splitlines()]
    assert any(line["event"] == "http.request" for line in lines)
    assert any(line["event"] == "auth.session.created" for line in lines)
    for forbidden in (token, "should-not-appear", member.email, "attacker@x.example"):
        assert forbidden not in output
