from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import new as hmac_new
from typing import Literal, Self
from uuid import UUID, uuid4

from jwt import decode as jwt_decode, encode as jwt_encode
from jwt.exceptions import InvalidTokenError
from pydantic import BaseModel, Field, ValidationError, field_serializer

JWT_ISSUER = "knowledgebase-storage"
ACCESS_TOKEN_TTL = timedelta(minutes=20)


def derive_key_with_csrf(base_key: str, csrf_token: str) -> str:
    return hmac_new(key=base_key.encode("utf-8"), msg=csrf_token.encode("utf-8"), digestmod=sha256).hexdigest()


class UserJWTToken(BaseModel):
    iss: str = JWT_ISSUER
    sub: str
    iat: datetime
    exp: datetime
    jti: UUID = Field(default_factory=uuid4)
    user: UUID

    key: str = Field(exclude=True)
    algorithm: str = Field(default="HS256", exclude=True)

    @classmethod
    def from_token(cls, token: str, key: str, algorithm: str = "HS256") -> Self:
        claims = jwt_decode(jwt=token, key=key, algorithms=[algorithm], issuer=JWT_ISSUER, options={"require": ["exp", "iat", "sub", "jti"]})
        try:
            user_token = cls(**claims, key=key, algorithm=algorithm)
        except ValidationError as err:
            raise InvalidTokenError from err
        return user_token

    @field_serializer("iat", "exp", when_used="json")
    def serialize_timestamp(self, value: datetime) -> int:
        return int(value.timestamp())

    @property
    def jwt(self) -> str:
        return jwt_encode(payload=self.model_dump(mode="json"), key=self.key, algorithm=self.algorithm)


class RefreshToken(UserJWTToken):
    sub: Literal["refresh"] = "refresh"

    @classmethod
    def issue(cls, user_id: UUID, ttl: timedelta, key: str) -> Self:
        iat = datetime.now(UTC).replace(microsecond=0)
        return cls(iat=iat, exp=iat + ttl, user=user_id, key=key)

    def to_access_token(self, csrf_token: str) -> AccessToken:
        iat = datetime.now(UTC).replace(microsecond=0)
        return AccessToken(
            iat=iat,
            exp=iat + ACCESS_TOKEN_TTL,
            user=self.user,
            key=derive_key_with_csrf(self.key, csrf_token),
            algorithm=self.algorithm,
        )


class AccessToken(UserJWTToken):
    sub: Literal["access"] = "access"
