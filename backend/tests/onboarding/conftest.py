"""The onboarding journey's world (P12.7): two organizations created by the operator's
bootstrap (``lattice org create``), each with its own identity provider client, an API
app wired to both providers, an import area, and the real worker.

Nothing is seeded: people, accounts and sources come from the product's own routes, and
the exports from ``data/synthetic/onboarding`` are uploaded as a customer would."""

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import select, text

from lattice.api.app import create_app
from lattice.authz import Document, readable_documents_predicate, resolve_authorization_context
from lattice.db.engine import Database
from lattice.identity.oidc import OidcSignIn
from lattice.imports.store import FilesystemImportStore
from lattice.jobs.worker import JobOutcome, Worker
from lattice.orgs.bootstrap import OrgCreated, OrgRequest, create_organization
from lattice.vault.keys import Keyring

from ..conftest import CSRF, make_settings
from ..identity.fake_idp import FakeIdp, oidc_client, public_resolver
from ..imports.archives import zip_tree

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "data/synthetic/onboarding"
KEY = "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE="
KEYS = {"LATTICE_SECRET_KEYS": f"k1:{KEY}", "LATTICE_SECRET_ACTIVE_KEY": "k1"}
WEB = "http://127.0.0.1:3000"
REASON = "Customer onboarding (synthetic)"


@dataclass(frozen=True)
class Tenant:
    slug: str
    issuer: str
    client_id: str
    secret: str
    admin_email: str
    admin_subject: str


WRENFIELD = Tenant(
    "wrenfield",
    "https://idp.wrenfield.example",
    "lattice-wrenfield",
    "wrenfield-client-secret-0123456789",
    "ada.quist@wrenfield.example",
    "wrn-ada",
)
CORVANE = Tenant(
    "corvane",
    "https://idp.corvane.example",
    "lattice-corvane",
    "corvane-client-secret-0123456789",
    "noor.amari@corvane.example",
    "crv-noor",
)


def org_request(tenant: Tenant, **changes: Any) -> OrgRequest:
    values: dict[str, Any] = {
        "slug": tenant.slug,
        "name": f"{tenant.slug.title()} (synthetic)",
        "issuer": tenant.issuer,
        "client_id": tenant.client_id,
        "subject_claim": "sub",
        "admin_email": tenant.admin_email,
        "admin_identifier": tenant.admin_subject,
        "admin_name": tenant.admin_email.split("@")[0].replace(".", " ").title(),
        "reason": REASON,
        "client_secret": SecretStr(tenant.secret),
    }
    values.update(changes)
    return OrgRequest(**values)


async def bootstrap(owner_db: Database, tenant: Tenant, **changes: Any) -> OrgCreated:
    """The operator's ``lattice org create``, with the client secret sealed in the vault."""
    return await create_organization(
        owner_db,
        org_request(tenant, **changes),
        keyring=Keyring.from_environment(KEYS),
        actor="operator:onboarding",
        resolver=public_resolver,
    )


@dataclass
class World:
    app: FastAPI
    idps: dict[str, FakeIdp]
    store: FilesystemImportStore
    worker_db: Database
    login_keys: dict[str, str] = field(default_factory=dict)

    def browser(self) -> httpx.AsyncClient:
        """A browser of its own (its own cookie jar) against the one app."""
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://testserver"
        )

    async def sign_in(self, browser: httpx.AsyncClient, slug: str, subject: str) -> str:
        """The authorization-code flow through the organization's provider: the final
        redirect (``/ask`` or ``/sign-in?error=…``)."""
        start = await browser.get(f"/v1/auth/oidc/{self.login_keys[slug]}/start")
        assert start.status_code == 303, start.text
        location = start.headers["location"]
        if location.startswith("/sign-in"):
            return location
        callback = self.idps[slug].authorize(location, subject)
        path = urlsplit(callback)
        assert f"{path.scheme}://{path.netloc}" == WEB, "callback leaves the public origin"
        done = await browser.get(path.path.removeprefix("/api") + "?" + path.query)
        assert done.status_code == 303, done.text
        return str(done.headers["location"])

    async def work(self) -> JobOutcome:
        outcome = await Worker(self.worker_db, embedding=None, store=self.store).run_once()
        assert outcome is not None, "no job was due"
        return outcome


@pytest.fixture
def onboarding_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in KEYS.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def world_factory(tmp_path: Path, worker_db: Database, onboarding_keys: None) -> Callable[..., Any]:
    @asynccontextmanager
    async def factory(**overrides: Any) -> AsyncIterator[World]:
        idps = {
            t.slug: FakeIdp(issuer=t.issuer, client_id=t.client_id, client_secret=t.secret)
            for t in (WRENFIELD, CORVANE)
        }
        by_host = {urlsplit(t.issuer).hostname: idps[t.slug] for t in (WRENFIELD, CORVANE)}

        def handler(request: httpx.Request) -> httpx.Response:
            return by_host[request.url.host].handler(request)

        root = tmp_path / "imports"
        root.mkdir(exist_ok=True)
        app = create_app(make_settings(imports_root=str(root), **overrides))
        app.state.oidc = OidcSignIn(
            app.state.db,
            oidc_client(handler),
            web_origin=WEB,
            secrets=app.state.ask_dependencies.secrets,
        )
        async with app.router.lifespan_context(app):
            yield World(app, idps, FilesystemImportStore(root=root), worker_db)

    return factory


# --- the product's own routes ------------------------------------------------------------


async def setup(browser: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    response = await browser.get("/v1/admin/setup")
    assert response.status_code == 200, response.text
    return {c["key"]: c for c in response.json()["categories"]}


def state(categories: dict[str, dict[str, Any]], key: str) -> tuple[str, str, str | None]:
    """(state, reason, the action's destination) of one category."""
    found = categories[key]
    action = found["action"]
    return found["state"], found["reason"], None if action is None else action["href"]


async def add_member(
    admin: httpx.AsyncClient, name: str, email: str, identifier: str
) -> dict[str, Any]:
    response = await admin.post(
        "/v1/admin/access/members",
        json={"email": email, "name": name, "identifier": identifier, "reason": REASON},
        headers=CSRF,
    )
    assert response.status_code == 201, response.text
    found: dict[str, Any] = response.json()
    return found


async def create_account(admin: httpx.AsyncClient, key: str, name: str) -> dict[str, Any]:
    response = await admin.post(
        "/v1/admin/access/accounts",
        json={"key": key, "name": name, "reason": REASON},
        headers=CSRF,
    )
    assert response.status_code == 201, response.text
    found: dict[str, Any] = response.json()
    return found


SOURCES: dict[str, dict[str, Any]] = {
    "handbook": {"connector_type": "document_repository", "authority": "working"},
    "support": {"connector_type": "ticketing", "authority": "working", "account_scoped": True},
    "slack": {"connector_type": "chat_export", "authority": "conversational"},
}


async def create_source(admin: httpx.AsyncClient, key: str) -> dict[str, Any]:
    body = {
        "key": key,
        "name": f"{key.title()} export",
        "default_sensitivity": "internal",
        "account_scoped": False,
        "reason": REASON,
        **SOURCES[key],
    }
    response = await admin.post("/v1/admin/sources", json=body, headers=CSRF)
    assert response.status_code == 201, response.text
    found: dict[str, Any] = response.json()
    return found


def export(key: str) -> tuple[bytes, str]:
    """The fixture export a customer uploads for the source ``key``: (body, content type).
    Tickets go up as one JSON Lines page; the others as zips of their folder."""
    if key == "support":
        return (DATA / "tickets/tickets.jsonl").read_bytes(), "application/x-ndjson"
    folder = {"handbook": "documents", "slack": "slack"}[key]
    return zip_tree(DATA / folder, wrapper=f"{folder}-export"), "application/zip"


async def upload(
    admin: httpx.AsyncClient, source_id: str, body: bytes, content_type: str = "application/zip"
) -> httpx.Response:
    return await admin.post(
        f"/v1/admin/sources/{source_id}/imports",
        content=body,
        headers={**CSRF, "Content-Type": content_type},
    )


async def detail(admin: httpx.AsyncClient, source_id: str) -> dict[str, Any]:
    response = await admin.get(f"/v1/admin/sources/{source_id}")
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return found


async def readable(app_db: Database, org: uuid.UUID, membership: uuid.UUID) -> set[tuple[str, str]]:
    """(source key, external ID) of every active document the member can read now, by
    the authorization predicate itself."""
    table = Document.__table__
    async with app_db.tenant_snapshot(org) as s:
        ctx = await resolve_authorization_context(s, organization_id=org, membership_id=membership)
        rows = await s.execute(
            text("SELECT id, key FROM sources WHERE organization_id = :o"), {"o": org}
        )
        sources: dict[uuid.UUID, str] = {r.id: str(r.key) for r in rows}
        found = await s.execute(
            select(table.c.source_id, table.c.external_id).where(
                readable_documents_predicate(ctx, table)
            )
        )
        return {(sources[s_id], str(ext)) for s_id, ext in found}
