"""Server-owned bearer identities; no caller-selected tenant or executable authority."""

import hashlib
import hmac
from typing import Annotated, Literal

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import AppError

Permission = Literal["read", "write", "review", "aws:sync"]


class ApiClient(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    principal_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    tenant_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    permissions: list[Permission] = Field(min_length=1, max_length=4)


class AuthenticationRequired(AppError):
    status_code = 401
    code = "authentication_required"
    message = "A valid bearer credential is required"


class AccessDenied(AppError):
    status_code = 403
    code = "access_denied"
    message = "The authenticated principal is not authorized for this operation"


def authenticate(settings, token: str) -> ApiClient:
    if not 32 <= len(token) <= 512:
        raise AuthenticationRequired()
    digest = hashlib.sha256(token.encode()).hexdigest()
    found = None
    for client in settings.api_clients:
        if hmac.compare_digest(client.token_sha256, digest):
            found = client
    if found is None:
        raise AuthenticationRequired()
    return found


bearer = HTTPBearer(auto_error=False, scheme_name="ScopedBearer")


def required_permissions(method, path):
    permissions = {"read"}
    if method not in {"GET", "HEAD", "OPTIONS"}:
        permissions.add("write")
    # Lifecycle transitions can accept risk, resolve or close a case. They are
    # human review decisions even though their URL does not end with /reviews.
    if path.endswith(("/review", "/reviews", "/transitions")):
        permissions.add("review")
    if "/aws/sync" in path:
        permissions.add("aws:sync")
    return permissions


def principal(
    request: Request,
    credential: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> ApiClient:
    if credential is None:
        raise AuthenticationRequired()
    client = authenticate(request.app.state.settings, credential.credentials)
    permissions = required_permissions(request.method, request.url.path)
    if not permissions.issubset(client.permissions):
        raise AccessDenied()
    request.state.principal = client
    if request.url.path != "/api/v1/aws/status":
        from app.repositories.live import LiveRepository

        LiveRepository(request.app.state.database, client.tenant_id).budget(
            client.principal_id,
            request.app.state.settings.api_query_limit_per_minute,
        )
    return client


Principal = Annotated[ApiClient, Depends(principal)]


def require_demo(principal: ApiClient):
    from app.models.events import DEMO_TENANT

    if principal.tenant_id != DEMO_TENANT:
        raise AccessDenied()
