from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import ProviderName


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    triage_provider: ProviderName = "demo"
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = Field(default="gpt-5.4-mini", min_length=1)
    openai_timeout_seconds: float = Field(default=20, gt=0, le=60)

    @model_validator(mode="after")
    def require_openai_key(self) -> Self:
        if self.triage_provider == "openai" and not self.openai_api_key.get_secret_value().strip():
            raise ValueError("Set OPENAI_API_KEY when TRIAGE_PROVIDER=openai.")
        return self
