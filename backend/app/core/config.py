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
        default=6,
        description="Maximum number of pages to scrape from the user's own website (1 homepage + up to 5 related pages).",
    )
    MAX_PAGES_PER_COMPETITOR: int = Field(
        default=6,
        description="Maximum number of pages to scrape per competitor site.",
    )
    CRAWLER_ACTOR_TIMEOUT_SECONDS: int = Field(
        default=240,
        description="Maximum execution timeout in seconds for Apify website content crawler actor.",
    )
    CRAWLER_PAGE_LOAD_TIMEOUT_SECONDS: int = Field(
        default=25,
        description="Maximum seconds to wait for page load in crawler actor.",
    )
    CRAWLER_MAX_REQUEST_RETRIES: int = Field(
        default=1,
        description="Maximum request retries for crawler actor on failed pages.",
    )
    CRAWLER_HARD_TIMEOUT_SECONDS: int = Field(
        default=90,
        description="Hard maximum execution timeout in seconds for crawler actor execution.",
    )
    SCRAPE_CACHE_ENABLED: bool = Field(
        default=True,
        description="Whether to reuse recently cached crawl results for the same domain.",
    )
    SCRAPE_CACHE_TTL_HOURS: int = Field(
        default=24,
        description="Time-to-live in hours for cached domain crawl results.",
    )
    CRAWLER_ENABLE_FALLBACK_CANDIDATES: bool = Field(
        default=True,
        description="Whether to attempt crawling fallback candidate URLs if primary selected pages fail.",
    )
    CRAWLER_BROWSER_ONLY_ON_HTTP_BLOCK: bool = Field(
        default=True,
        description="Whether to instruct browser fallback to use playwright:chrome directly when direct HTTP is known to be blocked.",
    )
    CRAWLER_MAX_CONCURRENCY_ACTOR: int = Field(
        default=6,
        ge=1,
        le=10,
        description="Maximum concurrent pages crawled inside the Apify actor.",
    )
    CRAWLER_PAGE_LOAD_TIMEOUT_SECS: int = Field(
        default=25,
        ge=5,
        le=120,
        description="Page load timeout in seconds inside the crawler actor.",
    )
    CRAWLER_NAVIGATION_TIMEOUT_SECS: int = Field(
        default=25,
        ge=5,
        le=120,
        description="Navigation timeout in seconds inside the crawler actor.",
    )
    PAGE_SELECTION_COLLECTION_CAP: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Maximum number of collection/category pages to select before prioritizing detail pages.",
    )
    ENABLE_REGIONAL_CONTEXT_DETECTION: bool = Field(
        default=True,
        description="Whether to detect regional/geo prefixes in crawled URLs and pass context to the LLM prompt.",
    )
    ENABLE_JSON_REPAIR_RETRY: bool = Field(
        default=True,
        description="Whether to attempt a single JSON repair retry if LLM structured parsing fails.",
    )
    ENABLE_EMERGENCY_OVERVIEW: bool = Field(
        default=False,
        description="Whether to generate an emergency best-effort overview on crawl failure.",
    )
    HOMEPAGE_RENDER_WAIT_MS: int = Field(
        default=1500,
        ge=0,
        le=10_000,
        description="Additional milliseconds to wait for dynamic JS hydration on the homepage (bounded to 0-10000).",
    )
    HOMEPAGE_MAX_SCROLL_HEIGHT_PIXELS: int = Field(
        default=2500,
        ge=0,
        le=10_000,
        description="Maximum pixel scroll height for triggering lazy-loaded homepage content (bounded to 0-10000).",
    )
    HTTP_FIRST_RETRIEVAL: bool = Field(
        default=True,
        description="Whether to attempt fast HTTP retrieval before falling back to browser-based crawling.",
    )
    HTTP_RETRIEVAL_TIMEOUT_SECONDS: float = Field(
        default=12.0,
        ge=1.0,
        le=60.0,
        description="Timeout in seconds for direct HTTP fetch requests.",
    )
    HTTP_MAX_CONCURRENCY: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Maximum concurrent HTTP requests for internal page crawling.",
    )
    MIN_PAGE_ADEQUACY_CHARS: int = Field(
        default=50,
        description="Minimum character threshold for general crawled page content to be considered adequate.",
    )
    MIN_HOMEPAGE_ADEQUACY_CHARS: int = Field(
        default=50,
        description="Minimum character threshold for homepage content to be considered adequate.",
    )
    MIN_FALLBACK_EXTRACT_CHARS: int = Field(
        default=500,
        description="Threshold below which layered fallback extraction is triggered for HTML content.",
    )
    MIN_USABLE_PAGE_CHARS: int = Field(
        default=300,
        description="Minimum character count for a scraped page to be treated as a full primary page in LLM synthesis.",
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
