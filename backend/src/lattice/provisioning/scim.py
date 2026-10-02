"""SCIM 2.0 ``User`` representation, parsing and PATCH application (RFC 7643/7644
subset; pure functions, no database).

What Lattice reads from a SCIM ``User`` (D-20: the identity provider owns these):

* ``externalId`` -- required on create, immutable: the value the OIDC connection's
  identifying claim carries for this person (ADR-015).
* ``userName`` -- unique per connection (case-insensitive).
* the e-mail -- the primary ``emails`` value, else ``userName`` when it is an address.
* the name -- ``displayName``, else ``name.formatted``, else given + family name, else
  ``userName``; ``name.givenName`` / ``name.familyName`` are kept for round trips.
* ``active`` -- ``true`` (default) or ``false``.

Everything else is ignored: roles, entitlements, groups, the enterprise extension,
titles, addresses. Nothing in a SCIM request can set clearance, account scope,
departments, groups, grants or the administrator capability (Lattice owns access).
Error details are fixed sentences: no submitted value is echoed.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Final

USER_SCHEMA: Final = "urn:ietf:params:scim:schemas:core:2.0:User"
LIST_SCHEMA: Final = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
PATCH_SCHEMA: Final = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ERROR_SCHEMA: Final = "urn:ietf:params:scim:api:messages:2.0:Error"
MEDIA_TYPE: Final = "application/scim+json"
MAX_RESULTS: Final = 100

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_FILTER = re.compile(
    r'^\s*(userName|externalId)\s+eq\s+"((?:[^"\\]|\\.){1,255})"\s*$', re.IGNORECASE
)
_EMAIL_FILTER_PATH = re.compile(r'^emails\[type eq "(work|home|other)"\]\.value$', re.IGNORECASE)


class ScimError(Exception):
    """A SCIM error response (RFC 7644 §3.12)."""

    def __init__(
        self,
        status: int,
        detail: str,
        scim_type: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.scim_type = scim_type
        self.headers = dict(headers or {})

    def body(self) -> dict[str, Any]:
        found: dict[str, Any] = {
            "schemas": [ERROR_SCHEMA],
            "status": str(self.status),
            "detail": self.detail,
        }
        if self.scim_type:
            found["scimType"] = self.scim_type
        return found


def invalid(detail: str, scim_type: str = "invalidValue") -> ScimError:
    return ScimError(400, detail, scim_type)


NOT_FOUND = "The resource was not found."


@dataclass(frozen=True)
class UserState:
    """The provider-owned attributes of one provisioned person."""

    external_id: str
    user_name: str
    email: str
    display_name: str
    given_name: str | None
    family_name: str | None
    active: bool


@dataclass(frozen=True)
class StoredUser:
    id: str
    state: UserState
    version: int
    created: datetime
    last_modified: datetime


def _text(value: Any, field: str, *, limit: int = 255, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise invalid(f"{field} is required.")
        return None
    if not isinstance(value, str):
        raise invalid(f"{field} must be a string.")
    stripped = value.strip()
    if not stripped:
        if required:
            raise invalid(f"{field} is required.")
        return None
    if len(stripped) > limit or _CONTROL.search(stripped):
        raise invalid(f"{field} is too long or contains control characters.")
    return stripped


def _active(value: Any) -> bool:
    # Some providers send booleans as strings ("True"/"False").
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise invalid("active must be a boolean.")


def _primary_email(emails: Any) -> str | None:
    if emails is None:
        return None
    if not isinstance(emails, list) or not all(isinstance(e, dict) for e in emails):
        raise invalid("emails must be a list of objects.")
    ordered = sorted(emails, key=lambda e: (e.get("primary") is not True, e.get("type") != "work"))
    for entry in ordered:
        value = _text(entry.get("value"), "emails.value", limit=320)
        if value:
            return value
    return None


def _email(value: str | None, user_name: str) -> str:
    candidate = value or (user_name if "@" in user_name else None)
    if candidate is None or not _EMAIL.fullmatch(candidate):
        raise invalid("A valid e-mail address is required (emails or userName).")
    return candidate


def _display_name(
    display: str | None, formatted: str | None, given: str | None, family: str | None, user: str
) -> str:
    joined = " ".join(p for p in (given, family) if p) or None
    return (display or formatted or joined or user)[:200]


def _name(resource: Mapping[str, Any]) -> Mapping[str, Any]:
    name = resource.get("name")
    if name is None:
        return {}
    if not isinstance(name, dict):
        raise invalid("name must be an object.")
    return name


def parse_user(resource: Any, *, current: UserState | None = None) -> UserState:
    """A full ``User`` (POST, PUT). On replace, ``externalId`` may be omitted (it is
    kept) but never changed."""
    if not isinstance(resource, dict):
        raise invalid("The request body must be a JSON object.", "invalidSyntax")
    schemas = resource.get("schemas")
    if not isinstance(schemas, list) or USER_SCHEMA not in schemas:
        raise invalid("schemas must include the core User schema.", "invalidSyntax")
    external_id = _text(resource.get("externalId"), "externalId", required=current is None)
    if current is not None:
        if external_id is not None and external_id != current.external_id:
            raise invalid("externalId cannot be changed.", "mutability")
        external_id = current.external_id
    assert external_id is not None  # noqa: S101 - required above when creating
    user_name = _text(resource.get("userName"), "userName", required=True)
    assert user_name is not None  # noqa: S101
    name = _name(resource)
    given = _text(name.get("givenName"), "name.givenName", limit=200)
    family = _text(name.get("familyName"), "name.familyName", limit=200)
    formatted = _text(name.get("formatted"), "name.formatted", limit=200)
    display = _text(resource.get("displayName"), "displayName", limit=200)
    active = _active(resource["active"]) if "active" in resource else True
    return UserState(
        external_id=external_id,
        user_name=user_name,
        email=_email(_primary_email(resource.get("emails")), user_name),
        display_name=_display_name(display, formatted, given, family, user_name),
        given_name=given,
        family_name=family,
        active=active,
    )


def _apply_attribute(state: UserState, path: str, value: Any, op: str) -> UserState:
    key = path.strip()
    lowered = key.lower()
    if op == "remove":
        if lowered == "name.givenname":
            return replace(state, given_name=None)
        if lowered == "name.familyname":
            return replace(state, family_name=None)
        if lowered in ("displayname", "name.formatted"):
            return replace(
                state,
                display_name=_display_name(
                    None, None, state.given_name, state.family_name, state.user_name
                ),
            )
        if lowered in ("username", "externalid", "active", "emails"):
            raise invalid(f"{key} cannot be removed.", "mutability")
        return state  # an attribute Lattice does not keep
    if lowered == "active":
        return replace(state, active=_active(value))
    if lowered == "username":
        user_name = _text(value, "userName", required=True)
        assert user_name is not None  # noqa: S101
        return replace(state, user_name=user_name)
    if lowered == "externalid":
        if _text(value, "externalId", required=True) != state.external_id:
            raise invalid("externalId cannot be changed.", "mutability")
        return state
    if lowered == "displayname":
        display = _text(value, "displayName", limit=200)
        return replace(
            state,
            display_name=_display_name(
                display, None, state.given_name, state.family_name, state.user_name
            ),
        )
    if lowered == "name.formatted":
        formatted = _text(value, "name.formatted", limit=200)
        return replace(state, display_name=formatted or state.display_name)
    if lowered == "name.givenname":
        return replace(state, given_name=_text(value, "name.givenName", limit=200))
    if lowered == "name.familyname":
        return replace(state, family_name=_text(value, "name.familyName", limit=200))
    if lowered == "name":
        if not isinstance(value, dict):
            raise invalid("name must be an object.")
        for sub, sub_value in value.items():
            state = _apply_attribute(state, f"name.{sub}", sub_value, op)
        return state
    if lowered == "emails":
        email = _primary_email(value if isinstance(value, list) else [value])
        return replace(state, email=_email(email, state.user_name))
    if _EMAIL_FILTER_PATH.fullmatch(key):
        email = _text(value, "emails.value", limit=320)
        return replace(state, email=_email(email, state.user_name))
    # Anything else (titles, addresses, the enterprise extension, roles, groups,
    # entitlements) is not kept and never affects access.
    return state


def apply_patch(state: UserState, body: Any) -> UserState:
    """RFC 7644 §3.5.2 for the attributes above; ``add`` and ``replace`` behave alike
    for these single-valued attributes."""
    if not isinstance(body, dict):
        raise invalid("The request body must be a JSON object.", "invalidSyntax")
    schemas = body.get("schemas")
    operations = body.get("Operations")
    if not isinstance(schemas, list) or PATCH_SCHEMA not in schemas:
        raise invalid("schemas must include the PatchOp schema.", "invalidSyntax")
    if not isinstance(operations, list) or not operations or len(operations) > 50:
        raise invalid("Operations must be a list of 1 to 50 operations.", "invalidSyntax")
    for operation in operations:
        if not isinstance(operation, dict):
            raise invalid("Each operation must be an object.", "invalidSyntax")
        op = str(operation.get("op", "")).lower()
        if op not in ("add", "replace", "remove"):
            raise invalid("op must be add, replace or remove.", "invalidSyntax")
        path = operation.get("path")
        value = operation.get("value")
        if path is None:
            if op == "remove":
                raise invalid("remove needs a path.", "noTarget")
            if not isinstance(value, dict):
                raise invalid("An operation without a path needs an object value.")
            for key, item in value.items():
                state = _apply_attribute(state, str(key), item, op)
        elif isinstance(path, str) and 0 < len(path) <= 200:
            state = _apply_attribute(state, path, value, op)
        else:
            raise invalid("path must be a string.", "invalidPath")
    return state


def parse_filter(value: str | None) -> tuple[str, str] | None:
    """``userName eq "x"`` or ``externalId eq "x"`` -> (attribute, value); None for no
    filter. Any other filter is refused (``invalidFilter``)."""
    if value is None or not value.strip():
        return None
    found = _FILTER.fullmatch(value)
    if found is None:
        raise invalid(
            'Only userName eq "..." and externalId eq "..." filters are supported.',
            "invalidFilter",
        )
    attribute = "userName" if found.group(1).lower() == "username" else "externalId"
    literal = re.sub(r"\\(.)", r"\1", found.group(2))
    return attribute, literal


def version_tag(version: int) -> str:
    return f'W/"{version}"'


def render_user(user: StoredUser, base_url: str) -> dict[str, Any]:
    state = user.state
    name: dict[str, Any] = {"formatted": state.display_name}
    if state.given_name:
        name["givenName"] = state.given_name
    if state.family_name:
        name["familyName"] = state.family_name
    return {
        "schemas": [USER_SCHEMA],
        "id": user.id,
        "externalId": state.external_id,
        "userName": state.user_name,
        "name": name,
        "displayName": state.display_name,
        "emails": [{"value": state.email, "type": "work", "primary": True}],
        "active": state.active,
        "meta": {
            "resourceType": "User",
            "created": user.created.isoformat(),
            "lastModified": user.last_modified.isoformat(),
            "location": f"{base_url}/Users/{user.id}",
            "version": version_tag(user.version),
        },
    }


def service_provider_config(base_url: str) -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": MAX_RESULTS},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": True},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "Bearer token",
                "description": "A SCIM token issued by the Lattice operator (lattice scim).",
                "primary": True,
            }
        ],
        "meta": {"resourceType": "ServiceProviderConfig", "location": base_url},
    }


def resource_types(base_url: str) -> dict[str, Any]:
    user = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
        "id": "User",
        "name": "User",
        "endpoint": "/Users",
        "schema": USER_SCHEMA,
        "meta": {"resourceType": "ResourceType", "location": f"{base_url}/ResourceTypes/User"},
    }
    return {"schemas": [LIST_SCHEMA], "totalResults": 1, "Resources": [user]}


def _attribute(
    name: str,
    *,
    required: bool = False,
    mutability: str = "readWrite",
    uniqueness: str = "none",
    type_: str = "string",
) -> dict[str, Any]:
    return {
        "name": name,
        "type": type_,
        "multiValued": False,
        "required": required,
        "caseExact": False,
        "mutability": mutability,
        "returned": "default",
        "uniqueness": uniqueness,
    }


def schemas(base_url: str) -> dict[str, Any]:
    name = _attribute("name", type_="complex")
    name["subAttributes"] = [_attribute(n) for n in ("formatted", "givenName", "familyName")]
    emails = _attribute("emails", type_="complex")
    emails["multiValued"] = True
    emails["subAttributes"] = [
        _attribute("value"),
        _attribute("type"),
        _attribute("primary", type_="boolean"),
    ]
    user = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"],
        "id": USER_SCHEMA,
        "name": "User",
        "description": "A person provisioned by the identity provider.",
        "attributes": [
            _attribute("userName", required=True, uniqueness="server"),
            _attribute("externalId", required=True, mutability="immutable"),
            name,
            _attribute("displayName"),
            emails,
            _attribute("active", type_="boolean"),
        ],
        "meta": {"resourceType": "Schema", "location": f"{base_url}/Schemas/{USER_SCHEMA}"},
    }
    return {"schemas": [LIST_SCHEMA], "totalResults": 1, "Resources": [user]}
