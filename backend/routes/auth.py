from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from backend.dependencies.auth import signedInUserDI
from backend.dependencies.headers import clientIpDI, csrfTokenDI, refreshTokenCookieDI, requiredCsrfTokenDI
from backend.schemas import AccessTokenResponse, APIKeyCreate, APIKeyCreated, APIKeyPublic, LoginRequest, UserPublic
from backend.services.auth import authServiceDI

router = APIRouter(prefix="/auth", tags=["auth"])


@router.head("/csrf", status_code=status.HTTP_204_NO_CONTENT)
async def issue_csrf_token(service: authServiceDI, csrf_token: csrfTokenDI = None) -> None:
    service.issue_csrf_token(csrf_token)


@router.post("/login")
async def login(payload: LoginRequest, csrf_token: requiredCsrfTokenDI, client_ip: clientIpDI, service: authServiceDI) -> AccessTokenResponse:
    return await service.login(payload, csrf_token, client_ip)


@router.get("/refresh")
async def refresh(csrf_token: requiredCsrfTokenDI, service: authServiceDI, refresh_token: refreshTokenCookieDI = None) -> AccessTokenResponse:
    return await service.refresh(refresh_token, csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(service: authServiceDI) -> None:
    service.logout()


@router.get("/me")
async def me(user: signedInUserDI) -> UserPublic:
    return UserPublic.model_validate(user, from_attributes=True)


@router.get("/api-keys")
async def list_api_keys(_: signedInUserDI, service: authServiceDI) -> list[APIKeyPublic]:
    return [APIKeyPublic.model_validate(api_key, from_attributes=True) for api_key in await service.list_api_keys()]


@router.post("/api-keys", status_code=status.HTTP_201_CREATED)
async def create_api_key(payload: APIKeyCreate, user: signedInUserDI, service: authServiceDI) -> APIKeyCreated:
    api_key, key = await service.create_api_key(user, payload)
    return APIKeyCreated(**api_key.model_dump(exclude={"key_digest"}), key=key)


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(key_id: UUID, _: signedInUserDI, service: authServiceDI) -> None:
    await service.delete_api_key(key_id)
