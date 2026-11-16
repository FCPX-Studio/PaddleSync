"""
Application configuration

This module exposes a singleton `settings` instance that can be imported
throughout the project (e.g., from config import settings).
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class PaddleSyncAPISettings(BaseSettings):
    # Load .env
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # FastAPI application
    APP_NAME: str = "PaddleSync"
    APP_DESCRIPTION: str = "Bridge between Paddle events and DB. Listen, validates and update data in Postgres."



settings = PaddleSyncAPISettings() # type: ignore