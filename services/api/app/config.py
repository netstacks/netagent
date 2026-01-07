"""API service configuration."""

import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings from environment variables."""

    # Database
    database_url: str = "postgresql://netagent:netagent_dev@localhost:5432/netagent"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Apigee OAuth
    apigee_client_id: str = ""
    apigee_client_secret: str = ""
    apigee_token_url: str = ""
    gemini_api_url: str = ""

    # Encryption
    encryption_key: str = ""

    # Email
    smtp_server: str = "localhost"
    smtp_port: int = 587
    smtp_from: str = "netagent@localhost"
    smtp_username: str = ""
    smtp_password: str = ""

    # Development mode
    dev_mode: bool = False

    class Config:
        env_file = ".env"


settings = Settings()
