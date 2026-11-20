from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        # .env also holds compose and dbmate variables.
        extra="ignore",
        hide_input_in_errors=True,
    )

    environment: Literal["local", "test", "production"] = "local"
    database_url: SecretStr = Field(min_length=1)
    db_pool_min_size: int = Field(default=1, ge=1)
    db_pool_max_size: int = Field(default=5, ge=1)
    # The built staff dashboard (`npm run build` in dashboard/); served at
    # /dashboard when the directory exists.
    dashboard_dir: Path = BASE_DIR / "dashboard" / "out"

    @model_validator(mode="after")
    def validate_pool_sizes(self) -> Self:
        if self.db_pool_min_size > self.db_pool_max_size:
            raise ValueError("db_pool_min_size must be <= db_pool_max_size")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
