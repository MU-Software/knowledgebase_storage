from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self

from backend.errors import ClientError
from backend.models import User
from backend.repositories.auth import UserRepository, userRepositoryDI
from backend.services import ServiceImpl

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession


class UserService(ServiceImpl[UserRepository]):
    repository: userRepositoryDI

    @classmethod
    def for_session(cls, session: AsyncSession) -> Self:
        return cls(repository=UserRepository(session=session))

    async def create_user(self, username: str, password: str) -> User:
        if await self.repository.find_by_username(username) is not None:
            ClientError.RESOURCE_ALREADY_EXISTS.format_msg(resource=self.repository.resource).raise_()
        return await self.repository.save(User(username=username, password=password))

    async def set_password(self, username: str, password: str) -> User:
        if (user := await self.repository.find_by_username(username)) is None:
            ClientError.RESOURCE_NOT_FOUND.format_msg(resource=self.repository.resource).raise_()
        user.password = password
        user.password_updated_at = datetime.now(UTC)
        return await self.repository.save(user)
