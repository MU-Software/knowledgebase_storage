from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from sqlalchemy import delete, func
from sqlmodel import col, desc, select

from backend.errors import ClientError
from backend.models import API_KEY_PREFIX, APIKey, LoginFailure, User
from backend.repositories import DBRepositoryImpl, OrderByType

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

API_KEY_DISPLAY_CHARS = 12


class UserRepository(DBRepositoryImpl[User]):
    model = User
    resource = "user"

    async def find_by_username(self, username: str) -> User | None:
        query = select(User).where(col(User.username) == username, User.not_deleted())
        return (await self.session.exec(query)).first()

    async def find_active(self, user_id: UUID) -> User | None:
        user = await self.session.get(User, user_id)
        return user if user is not None and not user.is_deleted else None


class APIKeyRepository(DBRepositoryImpl[APIKey]):
    model = APIKey
    resource = "api key"

    @property
    def order_by(self) -> OrderByType:
        return [desc(col(APIKey.created_at))]

    async def list_active(self) -> Sequence[APIKey]:
        return await self.list(query_filter=APIKey.not_deleted())

    async def issue(self, user_id: UUID, name: str, expires_in_days: int | None) -> tuple[APIKey, str]:
        key = API_KEY_PREFIX + secrets.token_urlsafe(32)
        api_key = APIKey(
            user_id=user_id,
            name=name,
            prefix=key[:API_KEY_DISPLAY_CHARS],
            key_digest=sha256(key.encode()).hexdigest(),
            deleted_at=datetime.now(UTC) + timedelta(days=expires_in_days) if expires_in_days else None,
        )
        return await self.save(api_key), key

    async def find_usable(self, key: str) -> tuple[APIKey, User] | None:
        query = (
            select(APIKey, User)
            .join(User, col(APIKey.user_id) == col(User.id))
            .where(col(APIKey.key_digest) == sha256(key.encode()).hexdigest(), APIKey.not_deleted(), User.not_deleted())
        )
        return (await self.session.exec(query)).first()

    async def soft_delete(self, key_id: UUID) -> None:
        api_key = await self.retrieve_by_id(key_id)
        if api_key.is_deleted:
            ClientError.RESOURCE_NOT_FOUND.format_msg(resource=self.resource).raise_()
        api_key.deleted_at = datetime.now(UTC)
        await self.save(api_key)


class LoginFailureRepository(DBRepositoryImpl[LoginFailure]):
    model = LoginFailure
    resource = "login failure"

    async def recent_counts(self, username: str, client_ip: str, since: datetime) -> tuple[int, int]:
        query = select(
            func.count().filter(col(LoginFailure.username) == username),
            func.count().filter(col(LoginFailure.client_ip) == client_ip),
        ).where(col(LoginFailure.created_at) >= since)
        by_username, by_ip = (await self.session.exec(query)).one()
        return int(by_username), int(by_ip)

    async def record(self, username: str, client_ip: str) -> None:
        await self.save(LoginFailure(username=username, client_ip=client_ip, created_at=datetime.now(UTC)))

    async def clear(self, username: str, client_ip: str) -> None:
        await self.session.exec(delete(LoginFailure).where(col(LoginFailure.username) == username, col(LoginFailure.client_ip) == client_ip))
        await self.session.commit()

    async def purge(self, before: datetime) -> int:
        result = await self.session.exec(delete(LoginFailure).where(col(LoginFailure.created_at) < before))
        await self.session.commit()
        return int(result.rowcount or 0)


userRepositoryDI = Annotated[UserRepository, Depends(UserRepository)]  # noqa: N816
apiKeyRepositoryDI = Annotated[APIKeyRepository, Depends(APIKeyRepository)]  # noqa: N816
loginFailureRepositoryDI = Annotated[LoginFailureRepository, Depends(LoginFailureRepository)]  # noqa: N816
