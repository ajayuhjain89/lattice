"""AI providers, credentials and egress policy (D-16; Phase 12, P12.5; phase-12-plan C3,
C4, C6 / D-34). Thin: parse, check the capability, call one service, map the result.

Under ``/v1/admin/providers``, administrators only; the capability is checked before
anything is read, so a member gets the same 403 whether or not the target exists, and a
provider or step-up of another organization (or of another administrator or session)
answers exactly as a random ID.

* ``GET`` -- the policy, every recorded provider (masked credentials), the egress lattice
  as the server reads it, the operator's registered generation targets, the step-up
  requirement and the AI setup status.
* ``changes/preview`` / ``changes`` -- Now -> After of an egress change and its commit; an
  escalation is refused here (``step_up_required``) and goes through ``step-ups``.
* ``step-ups`` -- start a fresh identity-provider re-authentication for one previewed
  escalation, read its result, apply it (once).
* ``POST`` (create), ``{id}/endpoint``, ``{id}/credential`` (set or replace),
  ``{id}/credential/revoke``, ``{id}/test``.

API keys arrive only in request bodies (``SecretStr``), are never logged (no request body
is), and never appear in a response.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Request, Response, status

from lattice.api.deps import AppSettings, CurrentPrincipal, Limits
from lattice.api.routes.auth import set_binding_cookie
from lattice.config import Environment
from lattice.db.engine import Database
from lattice.errors import ErrorCode, ErrorResponse, LatticeError
from lattice.identity.oidc import OidcSignIn
from lattice.identity.sessions import Principal
from lattice.providers import admin as service
from lattice.providers.admin_models import (
    ChangePreview,
    ChangePreviewRequest,
    ChangeRequest,
    CredentialRequest,
    EndpointRequest,
    NewProviderRequest,
    ProviderSettings,
    RevokeCredentialRequest,
    StepUpOut,
    StepUpRequest,
    StepUpStarted,
    TestOut,
)
from lattice.providers.generation_factory import GenerationFactory
from lattice.providers.secrets import SecretStore
from lattice.providers.tester import ProviderTester
from lattice.vault.keys import Keyring

router = APIRouter(tags=["admin"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
_WRITE_ERRORS: dict[int | str, dict[str, Any]] = {**_ERRORS, 429: {"model": ErrorResponse}}
_CHANGE_ERRORS: dict[int | str, dict[str, Any]] = {**_WRITE_ERRORS, 409: {"model": ErrorResponse}}
_CSRF = "Requires the X-Lattice-Request: 1 header."
API = "/v1/admin/providers"


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _secrets(request: Request) -> SecretStore:
    found: SecretStore = request.app.state.ask_dependencies.secrets
    return found


def _require_admin(principal: Principal) -> None:
    if not principal.is_admin:
        raise LatticeError(ErrorCode.PERMISSION_DENIED)


async def _admin_write(principal: Principal, limits: Limits) -> None:
    """Capability first (403 identical for any target), then the write limit."""
    _require_admin(principal)
    await limits.limiter.hit(
        principal.organization_id, principal.membership_id, limits.access_write
    )


async def _settings(
    request: Request, principal: Principal, settings: AppSettings
) -> ProviderSettings:
    factory: GenerationFactory = request.app.state.ask_dependencies.generation
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        return await service.provider_settings(
            session,
            principal.organization_id,
            principal.membership_id,
            organization_name=principal.organization_name,
            secrets=_secrets(request),
            factory=factory,
            production=settings.env is Environment.PRODUCTION,
        )


@router.get(
    API,
    response_model=ProviderSettings,
    responses=_ERRORS,
    summary="The organization's AI egress policy, providers and credentials (administrators; "
    "masked)",
    description="Never an API key, a secret reference or ciphertext: whether a key is set, "
    "who manages it, its last four characters (keys entered here), when it was set and its "
    "latest test. `lattice` is the server's reading of what may leave Lattice.",
)
async def get_providers(
    request: Request, principal: CurrentPrincipal, settings: AppSettings
) -> ProviderSettings:
    _require_admin(principal)
    return await _settings(request, principal, settings)


# --- egress changes -----------------------------------------------------------------------------


@router.post(
    f"{API}/changes/preview",
    response_model=ChangePreview,
    responses=_ERRORS,
    summary="Preview an egress change: Now -> After, and whether it is an escalation "
    "(administrators; changes nothing)",
    description="Computed by the database on the current policy (`policy_version`). An "
    f"escalation needs a fresh sign-in at the identity provider (`step-ups`). {_CSRF}",
)
async def post_change_preview(
    body: ChangePreviewRequest, request: Request, principal: CurrentPrincipal
) -> ChangePreview:
    _require_admin(principal)
    return await service.preview_change(
        _db(request), principal.organization_id, principal.membership_id, body.change
    )


@router.post(
    f"{API}/changes",
    response_model=ProviderSettings,
    responses=_CHANGE_ERRORS,
    summary="Apply an egress change that is not an escalation, with a reason "
    "(administrators; audited)",
    description="Lowering applies at once to the next request. `policy_version` must be "
    "the one previewed (409 `policy_version` otherwise). An escalation is refused (422 "
    f"`step_up_required`): start a step-up instead. {_CSRF}",
)
async def post_change(
    body: ChangeRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
    settings: AppSettings,
) -> ProviderSettings:
    await _admin_write(principal, limits)
    await service.apply_change(
        _db(request),
        principal.organization_id,
        principal.membership_id,
        principal.session_id,
        body,
    )
    return await _settings(request, principal, settings)


@router.post(
    f"{API}/step-ups",
    response_model=StepUpStarted,
    status_code=status.HTTP_201_CREATED,
    responses=_CHANGE_ERRORS,
    summary="Start a fresh identity-provider sign-in for one previewed escalation (administrators)",
    description="The change must be an escalation on the current `policy_version`; "
    "`confirmation` is the organization's name, typed; a reason. Open "
    "`authorization_url` and sign in again as yourself: you return to the AI providers page "
    "with `?step_up=<id>`. No session is created or changed. 422 `step_up` "
    "`unavailable_no_connection` / `unavailable_no_identity` when your organization cannot "
    f"step up (raising egress is then refused). {_CSRF}",
)
async def post_step_up(
    body: StepUpRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
    settings: AppSettings,
    response: Response,
) -> StepUpStarted:
    await _admin_write(principal, limits)
    sign_in: OidcSignIn = request.app.state.oidc
    started = await service.start_step_up(
        _db(request),
        sign_in,
        principal.organization_id,
        principal.membership_id,
        principal.session_id,
        organization_name=principal.organization_name,
        request=body,
    )
    set_binding_cookie(response, settings, started.binding)
    assert started.attempt_id is not None  # noqa: S101 - a step-up attempt
    return StepUpStarted(step_up_id=started.attempt_id, authorization_url=started.authorization_url)


@router.get(
    f"{API}/step-ups/{{step_up_id}}",
    response_model=StepUpOut,
    responses=_ERRORS,
    summary="Your step-up's result (administrators; only the one who started it, in the "
    "same session)",
)
async def get_step_up(
    step_up_id: uuid.UUID, request: Request, principal: CurrentPrincipal
) -> StepUpOut:
    _require_admin(principal)
    return await service.step_up_result(
        _db(request),
        principal.organization_id,
        principal.membership_id,
        principal.session_id,
        step_up_id,
    )


@router.post(
    f"{API}/step-ups/{{step_up_id}}/apply",
    response_model=ProviderSettings,
    responses=_CHANGE_ERRORS,
    summary="Apply the escalation a step-up authorized, once (administrators; audited)",
    description="Exactly the previewed change, on exactly the previewed policy version, in "
    "the session that stepped up, within five minutes of the sign-in. 409 `step_up` "
    "`grant_consumed` / `grant_expired` / `grant_mismatch` / `session_ended`, or "
    f"`policy_version` `changed`: preview and step up again. {_CSRF}",
)
async def post_step_up_apply(
    step_up_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
    settings: AppSettings,
) -> ProviderSettings:
    await _admin_write(principal, limits)
    await service.apply_step_up(
        _db(request),
        principal.organization_id,
        principal.membership_id,
        principal.session_id,
        step_up_id,
    )
    return await _settings(request, principal, settings)


# --- providers ------------------------------------------------------------------------------------


@router.post(
    API,
    response_model=ProviderSettings,
    status_code=status.HTTP_201_CREATED,
    responses=_CHANGE_ERRORS,
    summary="Configure a generation provider, disabled and unapproved, with a reason "
    "(administrators; audited)",
    description="gemini: a model from `supported` and its data terms. ollama: a target your "
    "operator registered (`targets`); its trust class follows the target. Nothing can be "
    f"sent to it until it is approved and enabled. {_CSRF}",
)
async def post_provider(
    body: NewProviderRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
    settings: AppSettings,
) -> ProviderSettings:
    await _admin_write(principal, limits)
    await service.create_provider(
        _db(request),
        principal.organization_id,
        principal.membership_id,
        body,
        production=settings.env is Environment.PRODUCTION,
    )
    return await _settings(request, principal, settings)


@router.put(
    f"{API}/{{provider_id}}/endpoint",
    response_model=ProviderSettings,
    responses=_CHANGE_ERRORS,
    summary="Move an Ollama provider to another registered target, with a reason "
    "(administrators; audited)",
    description="The trust class cannot change; the approval given for the previous "
    f"destination is withdrawn (approve again to send anything). {_CSRF}",
)
async def put_endpoint(
    provider_id: uuid.UUID,
    body: EndpointRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
    settings: AppSettings,
) -> ProviderSettings:
    await _admin_write(principal, limits)
    await service.set_endpoint(
        _db(request), principal.organization_id, principal.membership_id, provider_id, body
    )
    return await _settings(request, principal, settings)


@router.put(
    f"{API}/{{provider_id}}/credential",
    response_model=ProviderSettings,
    responses=_CHANGE_ERRORS,
    summary="Set or replace the provider's API key, with a reason (administrators; audited)",
    description="Encrypted at once (AES-256-GCM) for this organization; the previous key is "
    "revoked and destroyed. Never returned, logged or audited. A key changes no approval, "
    f"enablement or ceiling: it never lets anything more leave Lattice. {_CSRF}",
)
async def put_credential(
    provider_id: uuid.UUID,
    body: CredentialRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
    settings: AppSettings,
) -> ProviderSettings:
    await _admin_write(principal, limits)
    keyring: Keyring = request.app.state.keyring
    await service.set_credential(
        _db(request),
        keyring,
        principal.organization_id,
        principal.membership_id,
        provider_id,
        body,
    )
    return await _settings(request, principal, settings)


@router.post(
    f"{API}/{{provider_id}}/credential/revoke",
    response_model=ProviderSettings,
    responses=_CHANGE_ERRORS,
    summary="Revoke the provider's API key, with a reason (administrators; audited)",
    description=f"Excluded from the next request; its ciphertext is destroyed. {_CSRF}",
)
async def post_revoke_credential(
    provider_id: uuid.UUID,
    body: RevokeCredentialRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
    settings: AppSettings,
) -> ProviderSettings:
    await _admin_write(principal, limits)
    await service.revoke_credential(
        _db(request), principal.organization_id, principal.membership_id, provider_id, body
    )
    return await _settings(request, principal, settings)


@router.post(
    f"{API}/{{provider_id}}/test",
    response_model=TestOut,
    responses=_WRITE_ERRORS,
    summary="Test the provider's credential or endpoint (administrators)",
    description="No organization content is sent and nothing is generated (Gemini: the "
    "model's metadata; Ollama: the server's model list), through the outbound policy. A "
    "passing test approves nothing and permits nothing to be sent; the result is recorded "
    f"on the provider. {_CSRF}",
)
async def post_test(
    provider_id: uuid.UUID, request: Request, principal: CurrentPrincipal, limits: Limits
) -> TestOut:
    await _admin_write(principal, limits)
    tester: ProviderTester = request.app.state.provider_tester
    return await service.test_provider(
        _db(request), tester, _secrets(request), principal.organization_id, provider_id
    )
