from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, field_serializer, model_validator

RFC_7231_GMT_DATETIME_FORMAT = "%a, %d %b %Y %H:%M:%S GMT"


class Cookie(BaseModel):
    key: str
    value: str = ""
    expires: datetime | None = None
    path: str = "/"
    secure: bool = False
    httponly: bool = False
    samesite: Literal["lax", "strict", "none"] = "lax"

    @model_validator(mode="after")
    def validate_samesite(self) -> Self:
        if self.samesite == "none" and not self.secure:
            msg = "a cookie with samesite=none must be secure"
            raise ValueError(msg)
        return self

    @field_serializer("expires", when_used="always")
    def serialize_expires(self, value: datetime | None) -> str | None:
        return value.strftime(RFC_7231_GMT_DATETIME_FORMAT) if value else None
