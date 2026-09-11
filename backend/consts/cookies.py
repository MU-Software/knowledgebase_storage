from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from fastapi.params import Cookie

NEVER_EXPIRE_COOKIE_DATETIME = datetime.fromtimestamp(2**31 - 1, tz=UTC)


class CookieKey(Enum):
    @dataclass(frozen=True)
    class CookieKeyData:
        path: str
        expires: datetime | None = None
        alias: str | None = None

    CSRF_TOKEN = CookieKeyData(path="/api", expires=NEVER_EXPIRE_COOKIE_DATETIME, alias="kbstore_csrf")
    REFRESH_TOKEN = CookieKeyData(path="/api/auth", alias="kbstore_refresh")

    def get_name(self) -> str:
        return (self.name if self.value.alias is None else self.value.alias).lower()

    def as_fastapi_cookie(self) -> Cookie:
        return Cookie(alias=self.get_name(), include_in_schema=False)
