"""Centralized application configuration and settings for Competitor Marketing Agent.

Loads and validates all configuration and secret keys from environment variables
or a local .env file. Other modules should import `settings` or `get_settings()`
from this module rather than reading `os.environ` directly.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_env_file_paths() -> tuple[str, ...]:
    """Locate the .env file in the project hierarchy or working directory."""
    current = Path(__file__).resolve().parent
    discovered_paths: list[str] = [".env"]
    for parent in [current, *current.parents]:
        env_file = parent / ".env"
        if env_file.is_file():
            discovered_paths.append(str(env_file))
            break
    return tuple(discovered_paths)


# Scraper provider type alias structured to allow adding future providers (e.g. firecrawl, crawl4ai)
ScraperProvider = Literal["apify"]


class Settings(BaseSettings):
    """Application settings and secrets management via pydantic-settings."""

    model_config = SettingsConfigDict(
        env_file=_resolve_env_file_paths(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # LLM & OpenAI Settings
    # -------------------------------------------------------------------------
    OPENAI_API_KEY: SecretStr = Field(
        ...,
        description="Secret API key for OpenAI.",
    )
    LLM_MODEL: str = Field(
        ...,
        description="Canonical shared LLM model identifier (e.g. gpt-4o, gpt-6-luna). Swappable from .env.",
    )

    @property
    def OPENAI_MODEL(self) -> str:
        """Deprecated backward-compatible alias pointing to canonical LLM_MODEL."""
        return self.LLM_MODEL

    # -------------------------------------------------------------------------
    # Web Scraping Settings & Limits
    # -------------------------------------------------------------------------
    APIFY_API_TOKEN: SecretStr = Field(
        ...,
        description="Secret API token for Apify scraping service.",
    )
    SCRAPER_PROVIDER: ScraperProvider = Field(
        default="apify",
        description="Scraper provider to use. Structured for future provider additions.",
    )
    MAX_PAGES_USER_SITE: int = Field(
        default=8,
        description="Maximum number of pages to scrape from the user's own website.",
    )
    MAX_PAGES_PER_COMPETITOR: int = Field(
        default=8,
        description="Maximum number of pages to scrape per competitor site.",
    )
    MAX_COMPETITORS: int = Field(
        default=5,
        description="Maximum total number of competitors to analyze.",
    )
    MAX_AUTO_COMPETITORS: int = Field(
        default=3,
        description="Maximum number of competitors to automatically discover.",
    )

    # -------------------------------------------------------------------------
    # Database Settings
    # -------------------------------------------------------------------------
    DATABASE_URL: str = Field(
        ...,
        description="PostgreSQL database connection string.",
    )

    # -------------------------------------------------------------------------
    # Application Environment & Logging
    # -------------------------------------------------------------------------
    APP_ENV: Literal["development", "production"] = Field(
        default="development",
        description="Application environment mode (development or production).",
    )
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Logging verbosity level.",
    )

    # -------------------------------------------------------------------------
    # Optional Discovery Services
    # -------------------------------------------------------------------------
    SEARCH_API_KEY: SecretStr | None = Field(
        default=None,
        description="Optional API key for competitor search and discovery services.",
    )

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def _normalize_log_level(cls, v: str) -> str:
        """Normalize log level string to uppercase."""
        if isinstance(v, str):
            return v.strip().upper()
        return v


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton instance of application Settings.

    Fails fast at startup with a clear error message naming any missing
    required environment variables.
    """
    try:
        return Settings()
    except ValidationError as exc:
        missing_vars = [
            str(err["loc"][0]) for err in exc.errors() if err.get("type") == "missing"
        ]
        if missing_vars:
            names = ", ".join(missing_vars)
            raise RuntimeError(
                f"Startup configuration error: Missing required environment variable(s): {names}. "
                "Please configure them in your .env file or system environment."
            ) from exc
        raise


# Module-level settings instance for convenient direct import across the app
settings: Settings = get_settings()
