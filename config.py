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
        extra="ignore",
        hide_input_in_errors=True,  # never print secrets in validation errors
    )

    # FastAPI application
    APP_NAME: str = "PaddleSync"
    APP_DESCRIPTION: str = "Bridge between Paddle events and DB. Listen, validates and update data in Postgres."

    # Public base URL of the download storage (Cloudflare R2 bucket), e.g. "https://downloads.fcpxstudio.shop".
    # Product custom_data `download` paths are relative to it. Required: the service doesn't start without it
    DOWNLOAD_STORAGE_BASE_URL: str



settings = PaddleSyncAPISettings() # type: ignore