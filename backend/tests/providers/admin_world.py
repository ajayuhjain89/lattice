"""A world for provider administration tests (P12.5): two organizations, an OIDC
connection with an in-process identity provider, administrators bound to it, a fake
Gemini/Ollama network for provider tests -- no real network, no DNS."""

import ipaddress
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import FastAPI
from sqlalchemy import text

from lattice.db.engine import Database
from lattice.identity.oidc import OidcSignIn
from lattice.orgs.outbound_targets import add_target
from lattice.outbound import IpAddress, parse_target
from lattice.providers.secrets import EnvironmentSecretStore
from lattice.providers.tester import ProviderTester

from ..conftest import CSRF
from ..factories import create_member, create_oidc_connection, create_org
from ..identity.fake_idp import FakeIdp, oidc_client, public_resolver

KEY = "halden-login-key-0123456789"
ISSUER = "https://idp.halden.example"
KEYS = {
    "LATTICE_SECRET_KEYS": "a:QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE=",
    "LATTICE_SECRET_ACTIVE_KEY": "a",
}
API = "/v1/admin/providers"
GEMINI_KEY = "AIzaSy-test-gemini-key-0123456789-WXYZ"
NEW_GEMINI_KEY = "AIzaSy-another-gemini-key-9876543210-ABCD"
MODEL = "gemini-3.5-flash-lite"
PRIVATE = ipaddress.ip_address("10.20.30.40")


async def private_resolver(host: str, port: int) -> list[IpAddress]:
    return [PRIVATE]


async def any_resolver(host: str, port: int) -> list[IpAddress]:
    """Public for vendor hosts, private for the organization's own servers."""
    return [PRIVATE] if host.endswith(".internal") else [ipaddress.ip_address("93.184.215.14")]


@dataclass
class World:
    org: uuid.UUID
    connection: uuid.UUID
    lena: uuid.UUID  # administrator, directory identity "sub-lena"
    olga: uuid.UUID  # administrator, "sub-olga"
    maya: uuid.UUID  # member
    other_org: uuid.UUID
    vera: uuid.UUID  # the other organization's administrator


async def build_world(owner_db: Database) -> World:
    org = await create_org(owner_db, "halden")
    connection = await create_oidc_connection(owner_db, org, issuer=ISSUER, login_key=KEY)
    lena = await create_member(owner_db, org, "lena@halden.example", is_admin=True)
    olga = await create_member(owner_db, org, "olga@halden.example", is_admin=True)
    maya = await create_member(owner_db, org, "maya@halden.example")
    async with owner_db.tenant_transaction(org) as s:
        for member, subject in ((lena, "sub-lena"), (olga, "sub-olga")):
            await s.execute(
                text(
                    "INSERT INTO directory_identities (organization_id, connection_id, "
                    "membership_id, external_id, user_name, managed_by) "
                    "VALUES (:o, :c, :m, :x, :u, 'organization')"
                ),
                {"o": org, "c": connection, "m": member.membership_id, "x": subject, "u": subject},
            )
    other = await create_org(owner_db, "veridian")
    vera = await create_member(owner_db, other, "vera@veridian.example", is_admin=True)
    return World(
        org,
        connection,
        lena.membership_id,
        olga.membership_id,
        maya.membership_id,
        other,
        vera.membership_id,
    )


async def register_generation_target(owner_db: Database, org: uuid.UUID, url: str) -> uuid.UUID:
    async with owner_db.tenant_transaction(org) as s:
        found, _ = await add_target(
            s,
            org,
            target=parse_target(url),
            purpose="generation",
            reason="the organization's model server",
            actor="operator:test",
        )
    return found.id


@dataclass
class FakeProviders:
    """Gemini's models.get and Ollama's /api/tags, by host."""

    valid_keys: set[str] = field(default_factory=lambda: {GEMINI_KEY, NEW_GEMINI_KEY})
    ollama_models: list[str] = field(default_factory=lambda: ["qwen3.5:4b"])
    down: bool = False
    requests: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("unreachable", request=request)
        if request.url.host == "generativelanguage.googleapis.com":
            if request.headers.get("x-goog-api-key") not in self.valid_keys:
                return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})
            if request.url.path.endswith(f"/models/{MODEL}"):
                return httpx.Response(200, json={"name": f"models/{MODEL}"})
            return httpx.Response(404, json={"error": {"status": "NOT_FOUND"}})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in self.ollama_models]})
        return httpx.Response(404)


def configure(
    idp: FakeIdp,
    providers: FakeProviders,
    resolver: Callable[..., Any] = public_resolver,
) -> Callable[[FastAPI], None]:
    def apply(app: FastAPI) -> None:
        app.state.oidc = OidcSignIn(
            app.state.db,
            oidc_client(idp.handler, resolver=resolver),
            web_origin="http://127.0.0.1:3000",
            secrets=EnvironmentSecretStore(keyring=app.state.keyring),
        )
        app.state.provider_tester = ProviderTester(
            transport=httpx.MockTransport(providers.handler), resolver=any_resolver
        )

    return apply


def callback_path(url: str) -> str:
    parts = urlsplit(url)
    return parts.path.removeprefix("/api") + "?" + parts.query


def query_of(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


async def settings_of(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.get(API)
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return found


async def post(client: httpx.AsyncClient, path: str, body: Any = None) -> httpx.Response:
    return await client.post(f"{API}{path}", json=body, headers=CSRF)


async def put(client: httpx.AsyncClient, path: str, body: Any = None) -> httpx.Response:
    return await client.put(f"{API}{path}", json=body, headers=CSRF)


async def create_gemini(client: httpx.AsyncClient, training_opt_out: bool = True) -> str:
    response = await post(
        client,
        "",
        {
            "provider": "gemini",
            "model": MODEL,
            "training_opt_out": training_opt_out,
            "reason": "our generation provider",
        },
    )
    assert response.status_code == 201, response.text
    found = next(p for p in response.json()["providers"] if p["provider"] == "gemini")
    return str(found["id"])


async def set_key(
    client: httpx.AsyncClient,
    provider_id: str,
    key: str = GEMINI_KEY,
    expected: str | None = None,
    data_use: str = "paid",
) -> httpx.Response:
    return await put(
        client,
        f"/{provider_id}/credential",
        {
            "api_key": key,
            "data_use": data_use,
            "expected_credential_id": expected,
            "reason": "the organization's key",
        },
    )


async def change(
    client: httpx.AsyncClient, change_: dict[str, Any], version: int | None = None
) -> httpx.Response:
    if version is None:
        version = (await settings_of(client))["egress"]["version"]
    return await post(
        client, "/changes", {"change": change_, "policy_version": version, "reason": "policy"}
    )


async def preview(client: httpx.AsyncClient, change_: dict[str, Any]) -> dict[str, Any]:
    response = await post(client, "/changes/preview", {"change": change_})
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return found


async def step_up(
    client: httpx.AsyncClient,
    idp: FakeIdp,
    change_: dict[str, Any],
    *,
    subject: str = "sub-lena",
    confirmation: str = "Halden",
    version: int | None = None,
    reason: str = "approved by the security review",
) -> tuple[str, httpx.Response]:
    """Start a step-up and complete it at the identity provider; (step-up ID, callback)."""
    if version is None:
        version = (await settings_of(client))["egress"]["version"]
    started = await post(
        client,
        "/step-ups",
        {
            "change": change_,
            "policy_version": version,
            "reason": reason,
            "confirmation": confirmation,
        },
    )
    assert started.status_code == 201, started.text
    body = started.json()
    back = await client.get(callback_path(idp.authorize(body["authorization_url"], subject)))
    return str(body["step_up_id"]), back


def cell_map(settings: dict[str, Any]) -> dict[tuple[str, str], bool]:
    return {(c["provider_id"], c["level"]): c["permitted"] for c in settings["lattice"]}


async def rows(db: Database, org: uuid.UUID, sql: str, **params: Any) -> list[Any]:
    async with db.tenant_transaction(org) as s:
        return list((await s.execute(text(sql), params)).all())


async def events(db: Database, org: uuid.UUID, like: str) -> list[Any]:
    return await rows(
        db,
        org,
        "SELECT actor, action, target, before, after, reason FROM audit_events "
        "WHERE action LIKE :a ORDER BY created_at, id",
        a=like,
    )


async def client_for(
    app_client: Any, configure_: Callable[[FastAPI], None], email: str
) -> AsyncIterator[httpx.AsyncClient]:
    from ..factories import login

    async with app_client(configure_) as client:
        assert (await login(client, email)).status_code == 200
        yield client
