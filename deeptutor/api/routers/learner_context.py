"""Authenticated, versioned learner-context API for connected harnesses."""

from __future__ import annotations

from contextlib import contextmanager
import re
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from deeptutor.api.routers.auth import AUTH_ENABLED, TokenPayload, require_auth
from deeptutor.learner_context import credentials, service
from deeptutor.multi_user.context import get_current_user, reset_current_user, set_current_user
from deeptutor.multi_user.identity import get_user_by_id, load_users
from deeptutor.multi_user.models import CurrentUser, LOCAL_ADMIN_ID
from deeptutor.multi_user.paths import (
    ensure_scope_workspace,
    get_path_service_for_scope,
    local_admin_user,
    scope_for_user,
)
from deeptutor.services.workspace.context import account_workspace_context

router = APIRouter()
integration_router = APIRouter()


class InteractionRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=55)
    user_message: str = Field(min_length=1, max_length=4000)
    assistant_response: str = Field(default="", max_length=3000)

    @field_validator("event_id", "source")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be empty")
        if not re.fullmatch(r"[A-Za-z0-9._:-]+", value):
            raise ValueError("Use letters, numbers, dot, underscore, colon, or hyphen")
        return value


class CredentialCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    expires_in_days: int = Field(default=30, ge=1, le=90)
    scopes: list[str] = Field(default_factory=lambda: ["read", "write"])

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str]) -> list[str]:
        scopes = sorted(set(value))
        if not scopes or not set(scopes) <= {"read", "write"}:
            raise ValueError("Scopes must contain read and/or write")
        return scopes


class EnabledRequest(BaseModel):
    enabled: bool

class CollectionConsentRequest(BaseModel):
    enabled: bool


def _require_auth_enabled() -> None:
    if not AUTH_ENABLED:
        raise HTTPException(status_code=503, detail="Learner-context account controls require authentication to be enabled.")

def _require_account_admin(user_id: str) -> None:
    from deeptutor.multi_user.context import get_current_user

    user = get_current_user()
    if user.id != user_id or (user_id == LOCAL_ADMIN_ID and user.role != "admin"):
        raise HTTPException(status_code=403, detail="Learner-context account data must match the authenticated owner.")


def _chat_mastery_module():
    from deeptutor.services import chat_mastery

    return chat_mastery

def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@contextmanager
def _user_workspace(user_id: str):
    admin = local_admin_user()
    account = admin if user_id == admin.id else get_user_by_id(user_id)
    if account is None:
        raise HTTPException(status_code=401, detail="Credential owner no longer exists.")
    if account is admin:
        username, role = admin.username, "admin"
    else:
        if isinstance(account, tuple):
            username, record = account
        else:
            username, record = next(
                ((name, value) for name, value in load_users().items() if str(value.get("id") or "") == user_id),
                ("", {}),
            )
        if not username or str(record.get("id") or "") != user_id:
            raise HTTPException(status_code=401, detail="Credential owner no longer exists.")
        if bool(record.get("disabled", False)):
            raise HTTPException(status_code=401, detail="Integration credential owner is disabled.")
        role = str(record.get("role") or "user")
    is_admin = role == "admin"
    scope = scope_for_user(user_id, is_admin=is_admin)
    if not is_admin:
        ensure_scope_workspace(scope)
    service_paths = get_path_service_for_scope(scope)
    from deeptutor.services.workspace.context import WorkspaceScope, workspace_context

    user = CurrentUser(id=user_id, username=username, role=role, scope=scope)
    user_token = set_current_user(user)
    workspace_scope = WorkspaceScope("", service_paths.workspace_root, service_paths.get_workspace_dir())
    try:
        with account_workspace_context(scope.root):
            with workspace_context(workspace_scope):
                yield
    finally:
        reset_current_user(user_token)


def _require_integration(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    if not AUTH_ENABLED:
        if not _trusted_local_request(request):
            raise HTTPException(status_code=403, detail="Single-user learner integrations accept loopback requests only.")
        user_token = set_current_user(local_admin_user())
        try:
            credential = credentials.authenticate((authorization or "").partition(" ")[2].strip(), owner_id=LOCAL_ADMIN_ID)
        finally:
            reset_current_user(user_token)
        if credential is None or credential.get("user_id") != LOCAL_ADMIN_ID:
            raise HTTPException(status_code=401, detail="Invalid, expired, or revoked local integration credential.")
        return credential
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Bearer integration credential required.")
    owner_id = _token_owner_id(token.strip())
    if not owner_id:
        raise HTTPException(status_code=401, detail="Invalid integration credential owner.")
    credential = credentials.authenticate(token.strip(), owner_id=owner_id)
    if credential is None:
        raise HTTPException(status_code=401, detail="Invalid, expired, or revoked integration credential.")
    return credential


def _token_owner_id(token: str) -> str | None:
    prefix, separator, rest = token.partition(".")
    owner = prefix.removeprefix("dtlc_")
    credential_id, separator2, _secret = rest.partition(".")
    if not separator or not separator2 or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", owner):
        return None
    if not re.fullmatch(r"[a-f0-9]{24}", credential_id):
        return None
    return owner

def _trusted_local_request(request: Request) -> bool:
    if any(
        request.headers.get(name)
        for name in (
            "cf-access-jwt-assertion",
            "cf-connecting-ip",
            "cf-access-authenticated-user-email",
            "cf-ray",
            "x-forwarded-for",
            "x-real-ip",
            "x-authenticated-user",
            "remote-user",
            "x-forwarded-host",
            "x-forwarded-proto",
        )
    ):
        return False
    client = request.client
    host = client.host if client else ""
    if host in {"127.0.0.1", "::1"}:
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False

def _require_scope(credential: dict[str, Any], scope: str) -> str:
    if scope not in str(credential.get("scopes", "")).split(","):
        raise HTTPException(status_code=403, detail=f"Integration credential does not have {scope!r} scope.")
    return str(credential["user_id"])

@contextmanager
def _credential_workspace(credential: dict[str, Any], user_id: str):
    if not AUTH_ENABLED and user_id == LOCAL_ADMIN_ID:
        token = set_current_user(local_admin_user())
        try:
            yield
        finally:
            reset_current_user(token)
        return
    with _user_workspace(user_id):
        yield


@router.get("/v1/profile")
def get_style_profile(response: Response, _: TokenPayload | None = Depends(require_auth)):
    _require_auth_enabled()
    _no_store(response)
    return service.get_profile(include_records=True)


@router.get("/v1/prompt-context")
def get_prompt_context(response: Response, _: TokenPayload | None = Depends(require_auth)):
    _require_auth_enabled()
    _no_store(response)
    return {"schema_version": 1, "prompt_context": service.get_prompt_context()}


@router.get("/v1/records")
def get_style_records(response: Response, _: TokenPayload | None = Depends(require_auth)):
    _require_auth_enabled()
    _no_store(response)
    return {"records": service.get_profile(include_records=True)["records"]}


@router.delete("/v1/records/{record_id}")
def remove_style_record(record_id: int, _: TokenPayload | None = Depends(require_auth)):
    _require_auth_enabled()
    if not service.delete_record_for_current_user(get_current_user().id, record_id):
        raise HTTPException(status_code=404, detail="Style record not found.")
    return {"deleted": True}


@router.get("/v1/settings")
def get_style_settings(response: Response, _: TokenPayload | None = Depends(require_auth)):
    _require_auth_enabled()
    _no_store(response)
    user = get_current_user()
    profile = service.get_profile(path=user.scope.root / "user" / "chat_mastery.sqlite3")
    automatic_collection_enabled = _chat_mastery_module()._style_collection_enabled()
    return {
        "enabled": profile["enabled"],
        "signals": profile["signals"],
        "automatic_collection_enabled": automatic_collection_enabled,
    }


@router.put("/v1/settings")
def update_style_settings(
    payload: EnabledRequest,
    response: Response,
    _: TokenPayload | None = Depends(require_auth),
):
    _require_auth_enabled()
    _no_store(response)
    return {"enabled": service.set_enabled(payload.enabled)}

@router.put("/v1/automatic-collection")
def update_automatic_collection(
    payload: CollectionConsentRequest,
    response: Response,
    _: TokenPayload | None = Depends(require_auth),
):
    _require_auth_enabled()
    _no_store(response)
    return {"automatic_collection_enabled": _chat_mastery_module().set_style_collection_enabled(payload.enabled)}


@router.delete("/v1/records")
def remove_all_style_records(_: TokenPayload | None = Depends(require_auth)):
    _require_auth_enabled()
    return {"deleted": service.clear_records_for_current_user(get_current_user().id)}


@router.post("/v1/credentials", status_code=201)
def create_integration_credential(
    payload: CredentialCreateRequest,
    response: Response,
    _: TokenPayload | None = Depends(require_auth),
):
    _require_auth_enabled()
    _no_store(response)
    user = get_current_user()
    return credentials.create(
        user.id,
        payload.name,
        payload.expires_in_days,
        tuple(payload.scopes),
    )


@router.get("/v1/credentials")
def list_integration_credentials(response: Response, _: TokenPayload | None = Depends(require_auth)):
    _require_auth_enabled()
    _no_store(response)
    return {"credentials": credentials.list_for_user(get_current_user().id)}


@router.delete("/v1/credentials/{credential_id}")
def revoke_integration_credential(
    credential_id: str,
    response: Response,
    _: TokenPayload | None = Depends(require_auth),
):
    _require_auth_enabled()
    _no_store(response)
    user = get_current_user()
    revoked = credentials.revoke(user.id, credential_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Credential not found or already revoked.")
    return {"revoked": True}


@router.delete("/v1/credentials")
def revoke_all_integration_credentials(response: Response, _: TokenPayload | None = Depends(require_auth)):
    _require_auth_enabled()
    _no_store(response)
    user = get_current_user()
    return {"revoked": credentials.revoke_all(get_current_user().id)}

@integration_router.get("/profile")
def integration_profile(response: Response, credential: dict[str, Any] = Depends(_require_integration)):
    _no_store(response)
    user_id = _require_scope(credential, "read")
    with _credential_workspace(credential, user_id):
        return service.get_profile(include_records=False, include_quotes=False)


@integration_router.get("/prompt-context")
def integration_prompt_context(response: Response, credential: dict[str, Any] = Depends(_require_integration)):
    _no_store(response)
    user_id = _require_scope(credential, "read")
    with _credential_workspace(credential, user_id):
        return {
            "schema_version": 1,
            "prompt_context": service.get_prompt_context(
                automatic_collection_enabled=_chat_mastery_module()._style_collection_enabled()
            ),
        }


@integration_router.post("/interactions", status_code=202)
async def integration_record_interaction(
    payload: InteractionRequest,
    credential: dict[str, Any] = Depends(_require_integration),
):
    user_id = _require_scope(credential, "write")
    with _credential_workspace(credential, user_id):
        profile = service.get_profile()
        if not profile["enabled"]:
            return {"accepted": False, "reason": "opted_out"}
        if not _chat_mastery_module()._style_collection_enabled():
            return {"accepted": False, "reason": "classification_not_enabled"}
        from deeptutor.learner_context.classifier import classify_interaction

        label = await classify_interaction(
            payload.user_message,
            payload.assistant_response,
        )
        if label is None:
            return {"accepted": False, "reason": "no_learning_signal"}
        event_source = f"harness-{payload.source}"
        try:
            accepted = service.record_interaction_style(
                style=label,
                quote=payload.user_message[:200],
                source=event_source,
                event_id=payload.event_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"accepted": accepted, "duplicate": not accepted}


def record_external_style(
    *, user_id: str, event_id: str, source: str, style: str, quote: str = "", ts: float | None = None
) -> bool:
    """Internal tested adapter for classified signals; never accepts a target path."""
    with _user_workspace(user_id):
        return service.record_interaction_style(
            style=style,
            quote=quote,
            source=f"harness-{source}",
            event_id=event_id,
            ts=ts,
        )
