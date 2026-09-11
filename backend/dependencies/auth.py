from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from backend.dependencies.headers import apiKeyHeaderDI, bearerDI, csrfTokenDI
from backend.errors import AuthNError, AuthZError
from backend.models import API_KEY_PREFIX, User
from backend.services.auth import AuthContext, authServiceDI


def presented_api_key(header: apiKeyHeaderDI, credentials: bearerDI) -> str | None:
    bearer = credentials.credentials if credentials else None
    return header or (bearer if bearer and bearer.startswith(API_KEY_PREFIX) else None)


def presented_access_token(credentials: bearerDI) -> str | None:
    bearer = credentials.credentials if credentials else None
    return bearer if bearer and not bearer.startswith(API_KEY_PREFIX) else None


async def authenticate(
    service: authServiceDI,
    api_key: Annotated[str | None, Depends(presented_api_key)],
    access_token: Annotated[str | None, Depends(presented_access_token)],
    csrf_token: csrfTokenDI = None,
) -> AuthContext:
    if api_key:
        return await service.authenticate_api_key(api_key)
    if not (access_token and csrf_token):
        AuthNError.SIGNIN_REQUIRED.raise_()
    return await service.authenticate_access_token(access_token, csrf_token)


authContextDI = Annotated[AuthContext, Depends(authenticate)]  # noqa: N816


def require_login(auth_context: authContextDI) -> User:
    if not auth_context.signed_in or auth_context.user is None:
        AuthZError.PERMISSION_DENIED.raise_()
    return auth_context.user


signedInUserDI = Annotated[User, Depends(require_login)]  # noqa: N816
