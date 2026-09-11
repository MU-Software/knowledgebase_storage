from __future__ import annotations

from functools import cached_property, lru_cache
from pathlib import Path
from secrets import token_hex
from typing import Literal, Self

from pydantic import BaseModel, Field, PostgresDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio.engine import AsyncEngine, create_async_engine
from sqlalchemy.ext.asyncio.session import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession
from uvicorn.config import Config as UvicornConfig

BACKEND_DIR = Path(__file__).parent
PROJECT_DIR = BACKEND_DIR.parent
FRONTEND_PATH = BACKEND_DIR / "frontend" / "index.html"


class DatabaseSetting(BaseModel):
    dsn: PostgresDsn
    pool_size: int = 5
    max_overflow: int = 5

    @property
    def sqlalchemy_url(self) -> str:
        return str(self.dsn).replace("postgresql://", "postgresql+psycopg://", 1)


class ProjectSetting(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__", extra="ignore")

    debug: bool = False
    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8006
    notes_dir: Path = Path("/srv/knowledgebase/notes")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    https_enabled: bool = False
    secret_key: SecretStr = Field(default_factory=lambda: SecretStr(token_hex(32)))
    worker_api_key: SecretStr | None = None

    database: DatabaseSetting

    @property
    def cookie_samesite(self) -> Literal["lax", "strict", "none"]:
        return ("none" if self.https_enabled else "lax") if self.debug else "strict"

    @field_validator("notes_dir")
    @classmethod
    def resolve_notes_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @classmethod
    def from_dotenv(cls, env_file: str) -> Self:
        return cls(_env_file=env_file)

    @cached_property
    def async_engine(self) -> AsyncEngine:
        return create_async_engine(
            self.database.sqlalchemy_url,
            echo=self.debug,
            pool_pre_ping=True,
            pool_size=self.database.pool_size,
            max_overflow=self.database.max_overflow,
        )

    @cached_property
    def async_session_maker(self) -> async_sessionmaker[AsyncSession]:
        return async_sessionmaker(bind=self.async_engine, class_=AsyncSession, expire_on_commit=False)

    def to_uvicorn_config(self, app: str = "backend:create_app") -> UvicornConfig:
        return UvicornConfig(
            app=app,
            host=self.host,
            port=self.port,
            log_level=self.log_level.lower(),
            reload=self.debug,
            access_log=self.debug,
            factory=True,
        )


@lru_cache(maxsize=1)
def get_settings() -> ProjectSetting:
    env_file = PROJECT_DIR / "dotenv" / ".env.local"
    return ProjectSetting.from_dotenv(env_file.as_posix()) if env_file.exists() else ProjectSetting()
