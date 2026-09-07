import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    # Google OAuth 2.0
    GOOGLE_CLIENT_ID: str = Field(default="")
    GOOGLE_CLIENT_SECRET: str = Field(default="")
    GOOGLE_REDIRECT_URI: str = Field(default="http://localhost:8000/api/gmail/callback")

    # DB Settings
    DATABASE_URL: str = Field(default="sqlite+aiosqlite:///./job_hunter.db") # fallback sqlite, overridden if postgresql config exists
    
    # LLM Settings
    LLM_API_KEY: str = Field(default="")
    LLM_BASE_URL: str = Field(default="https://api.openai.com/v1")
    LLM_MODEL: str = Field(default="gpt-4o-mini")
    LLM_MAX_CONCURRENT_REQUESTS: int = Field(default=3)
    LLM_REQUESTS_PER_MINUTE: int = Field(default=60)
    LLM_MAX_RETRIES: int = Field(default=5)
    LLM_RETRY_BASE_DELAY: float = Field(default=2.0)
    LLM_RETRY_MAX_DELAY: float = Field(default=30.0)

    # API Rate Limiting
    API_RATE_LIMIT_PER_MINUTE: int = Field(default=120)

    # Server settings
    PORT: int = Field(default=8000)
    HOST: str = Field(default="127.0.0.1")

    # Upload configuration
    UPLOAD_DIR: str = Field(default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads"))

    # Config model for pydantic v2
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
