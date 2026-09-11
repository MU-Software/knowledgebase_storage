from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Response
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from backend.consts.cookies import CookieKey
from backend.dependencies import settingsDI
from backend.errors import AuthNError
from backend.utils.cookielib import Cookie

csrfTokenDI = Annotated[str | None, CookieKey.CSRF_TOKEN.as_fastapi_cookie()]  # noqa: N816
refreshTokenCookieDI = Annotated[str | None, CookieKey.REFRESH_TOKEN.as_fastapi_cookie()]  # noqa: N816
apiKeyHeaderDI = Annotated[str | None, Depends(APIKeyHeader(name="X-API-Key", auto_error=False))]  # noqa: N816
bearerDI = Annotated[HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))]  # noqa: N816


def require_csrf_token(csrf_token: csrfTokenDI = None) -> str:
    if not csrf_token:
        AuthNError.AUTHN_FAILED_AS_HEADER_NOT_PROVIDED.raise_()
    return csrf_token


requiredCsrfTokenDI = Annotated[str, Depends(require_csrf_token)]  # noqa: N816


def get_cookie_setter(settings: settingsDI, response: Response) -> Callable[[CookieKey, str, datetime | None], None]:
    def cookie_setter(key: CookieKey, value: str, expires: datetime | None = None) -> None:
        response.set_cookie(
            **Cookie(
                path=key.value.path,
                key=key.get_name(),
                value=value,
                expires=expires or key.value.expires,
                secure=settings.https_enabled,
                httponly=True,
                samesite=settings.cookie_samesite,
            ).model_dump(),
        )

    return cookie_setter


def get_cookie_deleter(settings: settingsDI, response: Response) -> Callable[[CookieKey], None]:
    def cookie_deleter(key: CookieKey) -> None:
        response.delete_cookie(
            **Cookie(
                path=key.value.path,
                key=key.get_name(),
                secure=settings.https_enabled,
                httponly=True,
                samesite=settings.cookie_samesite,
            ).model_dump(exclude={"value", "expires"}),
        )

    return cookie_deleter


cookieSetterDI = Annotated[Callable[[CookieKey, str, datetime | None], None], Depends(get_cookie_setter)]  # noqa: N816
cookieDeleterDI = Annotated[Callable[[CookieKey], None], Depends(get_cookie_deleter)]  # noqa: N816
