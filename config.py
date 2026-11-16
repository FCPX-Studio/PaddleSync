"""
Application configuration

This module exposes a singleton `settings` instance that can be imported
throughout the project (e.g., from config import settings).
"""
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class EventAPISettings(BaseSettings):
    # Load .env
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # FastAPI application
    APP_NAME: str = "EventAPI"
    APP_DESCRIPTION: str = "Expose event sending and subscription endpoints to external services"
    APP_VERSION: str = "1.0"

    # Redis
    REDIS_HOST: SecretStr
    REDIS_PORT: SecretStr
    REDIS_PASSWORD: SecretStr

    # Paddle
    PADDLE_ENVIRONMENT: Literal["sandbox", "production"] = "sandbox"
    PADDLE_WEBHOOK_SECRET: SecretStr



settings = EventAPISettings() # type: ignore