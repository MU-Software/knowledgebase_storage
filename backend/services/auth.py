from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hmac import compare_digest
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends

from backend.consts.cookies import CookieKey
from backend.dependencies import settingsDI
from backend.dependencies.headers import cookieDeleterDI, cookieSetterDI
from backend.errors import AuthNError
from backend.models import User
from backend.repositories.auth import UserRepository, apiKeyRepositoryDI, userRepositoryDI
from backend.repositories.setting import runtimeSettingRepositoryDI
from backend.schemas import AccessTokenResponse
from backend.services import ServiceImpl
from backend.utils.jwtlib import AccessToken, RefreshToken, UserJWTToken, derive_key_with_csrf

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from backend.models import APIKey
    from backend.schemas import APIKeyCreate, LoginRequest

API_KEY_TOUCH_INTERVAL = timedelta(minutes=5)
PLACEHOLDER_USER = User(username="placeholder", password=secrets.token_urlsafe(24) + "-0")


@dataclass(frozen=True)
class AuthContext:
    user: User | None = None
    signed_in: bool = False


class AuthService(ServiceImpl[UserRepository]):
    repository: userRepositoryDI
    api_keys: apiKeyRepositoryDI
    runtime: runtimeSettingRepositoryDI
    settings: settingsDI
    cookie_setter: cookieSetterDI
    cookie_deleter: cookieDeleterDI

    @property
    def secret_key(self) -> str:
        return self.settings.secret_key.get_secret_value()

    async def refresh_token_ttl(self) -> timedelta:
        return timedelta(hours=(await self.runtime.get()).session_ttl_hours)

    def issue_csrf_token(self, csrf_token: str | None) -> None:
        if not csrf_token:
            self.cookie_setter(CookieKey.CSRF_TOKEN, secrets.token_urlsafe(32), None)

    def issue_tokens(self, refresh: RefreshToken, csrf_token: str) -> AccessTokenResponse:
        self.cookie_setter(CookieKey.REFRESH_TOKEN, refresh.jwt, refresh.exp)
        return AccessTokenResponse(access_token=refresh.to_access_token(csrf_token).jwt)

    async def login(self, payload: LoginRequest, csrf_token: str) -> AccessTokenResponse:
        user = await self.repository.find_by_username(payload.username)
        (user or PLACEHOLDER_USER).compare_password(payload.password)
        if user is None:
            AuthNError.SIGNIN_FAILED.raise_()

        user.last_login_at = datetime.now(UTC)
        await self.repository.save(user)

        refresh = RefreshToken.issue(user.id, await self.refresh_token_ttl(), self.secret_key)
        return self.issue_tokens(refresh, csrf_token)

    async def refresh(self, refresh_token: str | None, csrf_token: str) -> AccessTokenResponse:
        if not refresh_token:
            AuthNError.INVALID_REFRESH_TOKEN.raise_()
        refresh = RefreshToken.from_token(refresh_token, self.secret_key)
        await self.signed_in_user(refresh)

        ttl = await self.refresh_token_ttl()
        if refresh.exp - datetime.now(UTC) <= ttl / 2:
            refresh = RefreshToken.issue(refresh.user, ttl, self.secret_key)
        return self.issue_tokens(refresh, csrf_token)

    def logout(self) -> None:
        for cookie_key in (CookieKey.REFRESH_TOKEN, CookieKey.CSRF_TOKEN):
            self.cookie_deleter(cookie_key)

    async def signed_in_user(self, token: UserJWTToken) -> User:
        user = await self.repository.find_active(token.user)
        if user is None or token.iat < user.password_updated_at.replace(microsecond=0):
            AuthNError.INVALID_TOKEN.raise_()
        return user

    async def authenticate_access_token(self, access_token: str, csrf_token: str) -> AuthContext:
        token = AccessToken.from_token(access_token, derive_key_with_csrf(self.secret_key, csrf_token))
        return AuthContext(user=await self.signed_in_user(token), signed_in=True)

    async def authenticate_api_key(self, key: str) -> AuthContext:
        worker_key = self.settings.worker_api_key.get_secret_value() if self.settings.worker_api_key else ""
        if worker_key and compare_digest(key.encode(), worker_key.encode()):
            return AuthContext()

        if (found := await self.api_keys.find_usable(key)) is None:
            AuthNError.INVALID_API_KEY.raise_()
        api_key, user = found
        now = datetime.now(UTC)
        if api_key.last_used_at is None or now - api_key.last_used_at >= API_KEY_TOUCH_INTERVAL:
            api_key.last_used_at = now
            await self.api_keys.save(api_key)
        return AuthContext(user=user)

    async def list_api_keys(self) -> Sequence[APIKey]:
        return await self.api_keys.list_active()

    async def create_api_key(self, user: User, payload: APIKeyCreate) -> tuple[APIKey, str]:
        return await self.api_keys.issue(user.id, payload.name, payload.expires_in_days)

    async def delete_api_key(self, key_id: UUID) -> None:
        await self.api_keys.soft_delete(key_id)


authServiceDI = Annotated[AuthService, Depends(AuthService)]  # noqa: N816
