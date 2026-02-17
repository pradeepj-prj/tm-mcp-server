"""MCP server configuration via pydantic-settings."""

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # MCP server host/port — CF sets $PORT automatically
    host: str = "0.0.0.0"
    port: int = int(os.environ.get("PORT", "8080"))

    # TM Skills API connection
    tm_api_base_url: str = "http://localhost:8000"
    tm_api_key: str = ""

    # Request timeout (seconds)
    tm_api_timeout: float = 30.0

    # Audit log SQLite database path
    audit_db_path: str = "audit.db"

    # CORS origins (comma-separated) — needed for the monitoring dashboard
    cors_origins: str = "http://localhost:5173,http://localhost:4173"


settings = Settings()
