"""Website Content Crawler Service using the Apify Website Content Crawler Actor.

Scrapes the content of specific web pages selected during the page-selection stage.
Uses the verified Apify Actor ``apify/website-content-crawler`` in zero-depth mode
(``maxCrawlDepth=0``) to ensure that only the requested URLs are scraped and no
unintended link-following or site-wide crawling occurs.

Key Responsibilities:
- Ingest URLs selected by ``page_selection_service.py`` (as URLs, SelectedPage,
  PageSelectionResult, or WebsitePipelineResult).
- Normalize, deduplicate, and validate input URLs.
- Exclude social media profile links (e.g. Instagram, Facebook, LinkedIn, Twitter/X).
- Configure the Apify Actor with bounded limits (zero crawl depth, max crawl pages,
  markdown/text extraction, DOM noise stripping, timeouts).
- Extract readable page text/markdown, page title, HTTP status, and metadata.
- Clean boilerplate, scripts, styles, cookie notices, and repetitive noise.
- Maintain source-to-result URL mapping and page-type classifications.
- Handle blocked pages, timeouts, partial failures, and empty responses safely.
- Never invent or fabricate page content when scraping fails.
- Return structured results leveraging the existing :class:`ScrapedPage` schema.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import datetime
import logging
import re
import time
import urllib.parse
from typing import Any, Literal, Sequence

import httpx
from apify_client import ApifyClient
from apify_client.errors import ApifyApiError
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.config import settings
from backend.app.schemas.enums import PageType
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.content_validation import (
    clean_boilerplate,
    clear_domain_http_blocks,
    extract_structured_fallback_text,
    is_blocked_content,
    is_content_adequate,
    is_domain_http_blocked,
    log_http_response_diagnostics,
    mark_domain_http_blocked,
)
from .page_selection_service import (
    PageSelectionResult,
    SelectedPage,
    WebsitePipelineResult,
)
from .url_extractor_service import classify_page_type

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants & Operational Limits
# ---------------------------------------------------------------------------

# Verified Apify Actor specifically designed for LLM/AI content extraction
WEBSITE_CONTENT_CRAWLER_ACTOR_ID: str = "apify/website-content-crawler"

# Apify Actor execution lifecycle statuses
TERMINAL_STATUSES: frozenset[str] = frozenset({
    "SUCCEEDED",
    "FAILED",
    "TIMED-OUT",
    "ABORTED",
})

NON_TERMINAL_STATUSES: frozenset[str] = frozenset({
    "READY",
    "RUNNING",
    "TIMING-OUT",
    "ABORTING",
})

# Default maximum number of pages to scrape per crawl run (configured via settings.MAX_PAGES_USER_SITE)
DEFAULT_MAX_PAGES: int = getattr(settings, "MAX_PAGES_USER_SITE", 6)

# Maximum runtime allowed for the Apify Actor run
DEFAULT_ACTOR_TIMEOUT_SECONDS: int = getattr(settings, "CRAWLER_ACTOR_TIMEOUT_SECONDS", 240)

# Retries for transient network/platform errors
MAX_TRANSIENT_RETRIES: int = 2

# Minimum character count for content to be considered meaningful
MIN_PAGE_CONTENT_CHARS: int = 50

# Maximum character count to prevent memory bloat or downstream LLM context overflow (~25k tokens)
MAX_PAGE_CONTENT_CHARS: int = 100_000

# CSS selectors for DOM elements removed by the actor prior to content conversion
NOISE_REMOVAL_CSS_SELECTOR: str = (
    "nav, header, footer, script, style, noscript, svg, aside, "
    "form[role='search'], form.search, form#search, form.newsletter, form#newsletter, "
    "[role='navigation'], [role='banner'], [role='contentinfo'], "
    ".cookie-banner, #cookie-notice, .ad-banner, .advertisement, "
    ".nav-menu, #menu, .sidebar, .share-buttons"
)

# Social media domains that must NEVER be crawled by this service
DISALLOWED_SOCIAL_DOMAINS: tuple[str, ...] = (
    "instagram.com",
    "facebook.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "tiktok.com",
    "pinterest.com",
    "reddit.com",
    "threads.net",
    "t.me",
    "whatsapp.com",
)

# Definitive challenge signatures that indicate an active challenge/block regardless of length
DEFINITIVE_BLOCK_SIGNALS: tuple[str, ...] = (
    "please verify you are a human",
    "verify you are human",
    "verify that you are human",
    "cf-challenge",
    "checking your browser before accessing",
    "attention required! | cloudflare",
    "you have been blocked",
    "why have i been blocked",
    "security check to continue",
    "complete the security check to continue",
    "ddos-guard protection",
)

# Ambiguous signals that indicate a block ONLY when page content is short / challenge-dominated
AMBIGUOUS_BLOCK_SIGNALS: tuple[str, ...] = (
    "captcha",
    "g-recaptcha",
    "hcaptcha",
    "cloudflare ray id",
    "ray id:",
    "access denied",
    "403 forbidden",
    "451 unavailable",
    "bot detected",
    "ddos-guard",
)

# Backward-compatible alias combining both sets
BLOCK_SIGNALS: tuple[str, ...] = DEFINITIVE_BLOCK_SIGNALS + AMBIGUOUS_BLOCK_SIGNALS

# Threshold: genuine challenge pages are concise stub pages (< 1500 chars)
MAX_CHALLENGE_PAGE_CHARS: int = 1500

# Targeted regex for cookie banners: bounded length, requiring consent action
_COOKIE_BANNER_REGEX = re.compile(
    r"(?i)(?:^|\n)[^\n]*?(?:we use cookies|this (?:website|site) uses cookies|our website uses cookies|cookies help us (?:deliver|improve)|by continuing to (?:browse|use)|cookie preferences|cookie policy|notice about cookies|we value your privacy)[^\n]*?(?:\n[^\n]*?){0,4}?(?:accept all(?: cookies)?|accept and continue|allow (?:all )?cookies|agree to (?:all )?cookies|manage (?:cookie )?preferences|reject all(?: cookies)?|decline cookies|got it|i accept|i agree|save preferences)[^\n]*?(?=\n|$)",
)

# Indicators of price or commercial offers to protect during paragraph deduplication
_PRICE_INDICATOR_REGEX = re.compile(
    r"(?:₹|\$|€|£|rs\.?|inr|usd|\bprice\b|\bdiscount\b|\bcoupon\b|\boff\b|\bfree\b|\b\d+(?:\.\d{2})?\s*(?:/mo|/yr|per month)\b)",
    re.IGNORECASE,
)

# Patterns identifying marketing, promotional, discount, shipping, or trust copy
_PROMOTIONAL_TEXT_REGEX = re.compile(
    r"(?i)("
    r"\b\d+%\s*off\b|"
    r"\b(?:sale|discount|discounts|special offer|limited time|flash sale|clearance|save\s*(?:up to|\$|₹|€|£|\d+))\b|"
    r"\b(?:coupon|promo code|discount code|voucher|use code|code\s*:\s*[A-Z0-9_-]+)\b|"
    r"\bfree\s+(?:shipping|delivery|returns|worldwide shipping|shipping over|delivery over)\b|"
    r"\b(?:standard shipping|express delivery|same[- ]day delivery|next[- ]day delivery)\b|"
    r"\b(?:money[- ]back guarantee|satisfaction guaranteed|\d+[- ]day (?:returns|guarantee|trial|money[- ]back)|risk[- ]free)\b|"
    r"\b(?:free trial|try free|get started free|claim your|shop the sale|shop now and save|subscribe & save|subscribe and save)\b|"
    r"\b(?:cruelty[- ]free|dermatologist[- ]tested|certified organic|100% natural|vegan)\b|"
    r"(?:₹|\$|€|£)\s*\d+(?:\.\d{2})?"
    r")"
)

# Container class/id/role identifying marketing, announcement, or banner containers
_PROMOTIONAL_CONTAINER_REGEX = re.compile(
    r"(?i)\b(?:promo|promotional|announcement|announcements|banner|offer|offers|deal|deals|discount|discounts|sale|coupon|top[-_]?bar|notification[-_]?bar|header[-_]?bar|hero|alert[-_]?bar)\b"
)

# Container class/id/role identifying pure navigation elements
_NAVIGATION_CONTAINER_REGEX = re.compile(
    r"(?i)\b(?:nav|navbar|navigation|nav[-_]?menu|site[-_]?nav|main[-_]?nav|header[-_]?nav|menu[-_]?list|nav[-_]?links|primary[-_]?menu|global[-_]?nav|user[-_]?menu|account[-_]?menu|dropdown[-_]?menu|main[-_]?menu|site[-_]?menu|header[-_]?menu|menu)\b"
)

# Slider/carousel cloned track elements that cause duplicate text
_SLIDER_CLONE_CLASS_REGEX = re.compile(
    r"(?i)\b(?:slick[-_]?cloned|swiper[-_]?slide[-_]?duplicate|clone|cloned)\b"
)

_MULTIPLE_NEWLINES_REGEX = re.compile(r"\n{3,}")
_MULTIPLE_SPACES_REGEX = re.compile(r"[ \t]{2,}")


# ---------------------------------------------------------------------------
# Structured Models
# ---------------------------------------------------------------------------

class FailedPageScrape(BaseModel):
    """Represents a target URL that could not be scraped or yielded unusable content."""

    model_config = ConfigDict(str_strip_whitespace=True)

    url: str = Field(description="The URL that failed to scrape.")
    error_message: str = Field(description="Description of the failure reason.")
    status_code: int | None = Field(
        default=None,
        description="HTTP status code reported by the server, if known.",
    )
    page_type: PageType = Field(
        default=PageType.UNKNOWN,
        description="Original page type classification.",
    )


class WebsiteCrawlResult(BaseModel):
    """Structured result returned by the website content crawler service."""

    model_config = ConfigDict(str_strip_whitespace=True)

    website_url: str = Field(description="Primary website URL or base domain.")
    status: Literal["success", "partial", "empty", "failed"] = Field(
        description="Overall crawl outcome: success (all scraped), partial (some failed), empty (no content), failed (actor error).",
    )
    scraped_pages: list[ScrapedPage] = Field(
        default_factory=list,
        description="Successfully scraped pages containing readable text/markdown content.",
    )
    failed_pages: list[FailedPageScrape] = Field(
        default_factory=list,
        description="List of requested URLs that failed to scrape or yielded empty content.",
    )
    total_requested: int = Field(
        default=0,
        description="Total unique, valid target URLs submitted for scraping.",
    )
    total_scraped: int = Field(
        default=0,
        description="Number of successfully scraped pages.",
    )
    total_failed: int = Field(
        default=0,
        description="Number of failed page scrapes.",
    )
    has_usable_content: bool = Field(
        default=False,
        description="True if at least one page produced usable content.",
    )
    actor_run_id: str | None = Field(
        default=None,
        description="Apify Actor run ID for debugging, or None if no run was executed.",
    )
    actor_status: str | None = Field(
        default=None,
        description="Raw Apify Actor execution status (e.g. SUCCEEDED, TIMED-OUT, FAILED, ABORTED).",
    )
    execution_time_seconds: float = Field(
        default=0.0,
        description="Total wall-clock duration in seconds.",
    )
    error_message: str | None = Field(
        default=None,
        description="High-level error description if the actor run failed.",
    )
    # Lightweight retrieval metrics
    http_attempted: int = Field(
        default=0,
        description="Number of HTTP requests actually attempted over the wire.",
    )
    http_succeeded: int = Field(
        default=0,
        description="Number of pages successfully retrieved via direct HTTP.",
    )
    http_failed: int = Field(
        default=0,
        description="Number of attempted HTTP requests that failed or were inadequate.",
    )
    http_429_count: int = Field(
        default=0,
        description="Number of HTTP 429 rate-limited responses received.",
    )
    browser_fallback_requested: int = Field(
        default=0,
        description="Number of pages submitted to the Apify browser fallback crawler.",
    )
    browser_fallback_succeeded: int = Field(
        default=0,
        description="Number of pages successfully retrieved via browser fallback.",
    )


@dataclass
class _PageDiagnostic:
    """Internal diagnostic record for structured per-page crawler performance logging."""

    requested_url: str
    final_url: str
    status: str  # "SUCCESS" or "FAILED"
    http_status: int | None = None
    duration_seconds: float | None = None
    char_count: int = 0
    is_truncated: bool = False
    error_category: str | None = None
    error_reason: str | None = None
    retrieval_method: str = "browser"  # "http" or "browser"


def _extract_item_duration(item: dict[str, Any]) -> float | None:
    """Extract measured per-page execution duration in seconds if exposed by the actor dataset.

    Returns None if not provided by the actor (never guesses or approximates).
    """
    if not isinstance(item, dict):
        return None

    crawl_obj = item.get("crawl") if isinstance(item.get("crawl"), dict) else {}

    # 1. Direct duration in milliseconds
    for key in (
        "requestHandlerDurationMillis",
        "durationMillis",
        "requestDurationMillis",
        "handlerDurationMillis",
        "crawlDurationMillis",
    ):
        val = crawl_obj.get(key) if key in crawl_obj else item.get(key)
        if val is not None:
            try:
                num = float(val)
                if num > 0:
                    return round(num / 1000.0, 2)
            except (ValueError, TypeError):
                pass

    # 2. Duration in seconds
    for key in ("executionTime", "duration", "durationSecs", "requestDurationSecs"):
        val = crawl_obj.get(key) if key in crawl_obj else item.get(key)
        if val is not None:
            try:
                num = float(val)
                if num > 0:
                    return round(num, 2)
            except (ValueError, TypeError):
                pass

    # 3. ISO timestamp difference
    started = crawl_obj.get("requestStartedAt") or item.get("startedAt")
    finished = crawl_obj.get("requestFinishedAt") or item.get("finishedAt")
    if started and finished:
        try:
            t_start = datetime.datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            t_finish = datetime.datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
            dur = (t_finish - t_start).total_seconds()
            if dur >= 0:
                return round(dur, 2)
        except Exception:
            pass

    return None


def _categorize_error(error_message: str, status_code: int | None = None) -> tuple[str, str]:
    """Determine a structured error category and concise reason for a failed page scrape.

    Returns:
        (category, concise_reason)
    """
    msg = error_message.strip() if error_message else ""
    msg_lower = msg.lower()

    if status_code is not None and status_code >= 400:
        if status_code == 404:
            return "HTTP_NOT_FOUND", "HTTP 404 Not Found"
        if status_code in (401, 403):
            return "HTTP_FORBIDDEN", f"HTTP {status_code} Forbidden/Unauthorized"
        if status_code == 429:
            return "RATE_LIMITED", "HTTP 429 Too Many Requests"
        if status_code >= 500:
            return "HTTP_SERVER_ERROR", f"HTTP {status_code} Server Error"
        return "HTTP_ERROR", f"HTTP {status_code} Error"

    if any(k in msg_lower for k in ("blocked", "access denied", "challenge", "captcha", "cloudflare", "waf")):
        return "ACCESS_DENIED", "Bot challenge or WAF access denial detected"

    if "too short" in msg_lower or "empty" in msg_lower:
        return "EMPTY_CONTENT", msg or "Extracted content is empty or below minimum threshold"

    if "timed-out" in msg_lower or "timed out" in msg_lower or "timeout" in msg_lower:
        return "ACTOR_TIMEOUT", "Crawler actor timed out before processing page"

    if "social media" in msg_lower:
        return "DISALLOWED_DOMAIN", "Excluded social media domain"

    if "invalid" in msg_lower or "non-http" in msg_lower:
        return "INVALID_URL", "Invalid or non-HTTP URL format"

    if "not returned" in msg_lower:
        return "NOT_RETURNED", "Page was not returned in crawler dataset output"

    if "apify_api_token" in msg_lower or "placeholder" in msg_lower:
        return "AUTH_CONFIG_ERROR", msg or "Apify authentication token configuration error"

    if "nonterminal" in msg_lower:
        return "DEADLINE_EXPIRED", "Actor remained nonterminal when deadline expired"

    return "CRAWL_FAILURE", msg or "Unknown crawl failure"


def _log_crawler_diagnostics(
    *,
    website_url: str,
    overall_status: str,
    actor_status: str | None,
    total_requested: int,
    total_scraped: int,
    total_failed: int,
    wall_clock_seconds: float,
    diagnostics: list[_PageDiagnostic],
    actor_run_id: str | None = None,
    error_message: str | None = None,
) -> None:
    """Log structured per-page diagnostics and aggregate summary for a crawler run."""
    run_info = f" (run_id='{actor_run_id}')" if actor_run_id else ""
    actor_status_label = actor_status or "none"

    logger.info(
        "=== [CRAWLER PER-PAGE DIAGNOSTICS: %s]%s ===",
        website_url,
        run_info,
    )

    for idx, d in enumerate(diagnostics, start=1):
        redirect_part = (
            f" | redirect_to='{d.final_url}'"
            if d.final_url and d.final_url != d.requested_url
            else ""
        )
        http_part = f"HTTP {d.http_status}" if d.http_status is not None else "HTTP status: unavailable"
        timing_part = (
            f"{d.duration_seconds:.2f}s"
            if d.duration_seconds is not None
            else "unavailable (not reported by actor dataset)"
        )

        if d.status == "SUCCESS":
            trunc_part = " [truncated]" if d.is_truncated else ""
            logger.info(
                "  [%d/%d] SUCCESS | url='%s'%s | method=%s | status=%s | duration=%s | content=%d chars%s",
                idx,
                len(diagnostics),
                d.requested_url,
                redirect_part,
                d.retrieval_method,
                http_part,
                timing_part,
                d.char_count,
                trunc_part,
            )
        elif d.status == "SKIPPED":
            logger.info(
                "  [%d/%d] SKIPPED | url='%s'%s | method=%s | status=%s | duration=%s | content=0 chars | reason=[%s] %s",
                idx,
                len(diagnostics),
                d.requested_url,
                redirect_part,
                d.retrieval_method,
                http_part,
                timing_part,
                d.error_category or "RATE_LIMITED",
                d.error_reason or "Skipped",
            )
        else:
            logger.warning(
                "  [%d/%d] FAILED  | url='%s'%s | method=%s | status=%s | duration=%s | content=%d chars | error=[%s] %s",
                idx,
                len(diagnostics),
                d.requested_url,
                redirect_part,
                d.retrieval_method,
                http_part,
                timing_part,
                d.char_count,
                d.error_category or "UNKNOWN",
                d.error_reason or "Unspecified error",
            )

    # Compute aggregate metrics
    total_content_chars = sum(d.char_count for d in diagnostics if d.status == "SUCCESS")
    measured_durations = [d.duration_seconds for d in diagnostics if d.duration_seconds is not None]
    http_success_count = sum(1 for d in diagnostics if d.status == "SUCCESS" and d.retrieval_method == "http")
    browser_success_count = sum(1 for d in diagnostics if d.status == "SUCCESS" and d.retrieval_method == "browser")

    if measured_durations:
        min_d = min(measured_durations)
        max_d = max(measured_durations)
        avg_d = sum(measured_durations) / len(measured_durations)
        durations_str = (
            f"measured for {len(measured_durations)}/{len(diagnostics)} page(s) "
            f"(min={min_d:.2f}s, avg={avg_d:.2f}s, max={max_d:.2f}s)"
        )
    else:
        durations_str = "unavailable (actor dataset items do not expose per-page request duration; wall-clock time was measured)"

    status_flags: list[str] = []
    if overall_status == "partial":
        status_flags.append("partial results recovered")
    if actor_status == "TIMED-OUT":
        status_flags.append("actor timed out")
    elif actor_status in ("FAILED", "ABORTED"):
        status_flags.append(f"actor {actor_status.lower()}")

    flags_part = f" [{', '.join(status_flags)}]" if status_flags else ""

    logger.info(
        "=== [CRAWLER AGGREGATE SUMMARY: %s] ===\n"
        "  • Crawl Outcome: %s (Actor Status: %s)%s\n"
        "  • Total Requested URLs: %d\n"
        "  • Successful Pages: %d (HTTP: %d, Browser: %d)\n"
        "  • Failed Pages: %d\n"
        "  • Total Extracted Content: %s characters across %d successful page(s)\n"
        "  • Crawler Wall-Clock Duration: %.2fs\n"
        "  • Per-Page Durations: %s%s",
        website_url,
        overall_status.upper(),
        actor_status_label,
        flags_part,
        total_requested,
        total_scraped,
        http_success_count,
        browser_success_count,
        total_failed,
        f"{total_content_chars:,}",
        total_scraped,
        wall_clock_seconds,
        durations_str,
        f"\n  • High-Level Error: {error_message}" if error_message and overall_status in ("failed", "partial") else "",
    )


# ---------------------------------------------------------------------------
# Internal Helper Functions
# ---------------------------------------------------------------------------

def _is_social_media_url(url: str) -> bool:
    """Check if the provided URL belongs to a known social media network."""
    try:
        parsed = urllib.parse.urlsplit(url.strip())
        netloc = parsed.netloc.lower()
        # Strip port if present
        if ":" in netloc:
            netloc = netloc.split(":")[0]
        return any(
            netloc == domain or netloc.endswith("." + domain)
            for domain in DISALLOWED_SOCIAL_DOMAINS
        )
    except Exception:
        return False


def _normalize_target_url(url: str) -> str | None:
    """Validate and normalize a web URL. Returns None if invalid or non-HTTP."""
    cleaned = url.strip()
    if not cleaned:
        return None
    try:
        parsed = urllib.parse.urlsplit(cleaned)
        if parsed.scheme.lower() not in ("http", "https"):
            return None
        if not parsed.netloc:
            return None
        netloc = parsed.netloc.lower()
        path = parsed.path or "/"
        # Clean trailing slashes for non-root paths to ensure consistent matching
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")
        # Rebuild standard URL without fragment
        return urllib.parse.urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))
    except Exception:
        return None


def _normalize_domain_host(url: str) -> str:
    """Extract lowercase host stripped of www. prefix for caching and block checks."""
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlsplit(url)
        netloc = (parsed.netloc or parsed.path).lower()
        if ":" in netloc:
            netloc = netloc.split(":", 1)[0]
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc.strip()
    except Exception:
        return ""


@dataclass
class _ScrapeCacheEntry:
    cached_at: float
    result: WebsiteCrawlResult


_SCRAPE_CACHE: dict[str, _ScrapeCacheEntry] = {}


def _is_scrape_cache_enabled() -> bool:
    try:
        val = getattr(settings, "SCRAPE_CACHE_ENABLED", True)
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, str)):
            return str(val).lower() in ("true", "1", "yes")
        return False
    except Exception:
        return False


def _build_crawl_cache_key(url: str, target_urls: Sequence[str] | None = None) -> str:
    host = _normalize_domain_host(url)
    if not host:
        return ""
    # Include normalized path root of base url if regional prefix present
    parsed_base = urllib.parse.urlsplit(url)
    base_path_root = parsed_base.path.strip("/").split("/")[0].lower() if parsed_base.path.strip("/") else ""
    key_prefix = f"{host}/{base_path_root}" if base_path_root in ("en-us", "us", "in", "en-in", "uk", "en-gb", "ca", "en-ca", "au", "en-au") else host

    if not target_urls:
        return key_prefix
    norm_urls = sorted([
        _normalize_domain_host(u) + (urllib.parse.urlsplit(u).path.lower().rstrip("/") or "/")
        for u in target_urls if u
    ])
    return f"{key_prefix}::" + ",".join(norm_urls)


def get_cached_crawl(url: str, target_urls: Sequence[str] | None = None) -> WebsiteCrawlResult | None:
    """Retrieve cached WebsiteCrawlResult if enabled and unexpired."""
    try:
        if not _is_scrape_cache_enabled():
            return None
        host = _normalize_domain_host(url)
        if not host:
            return None

        cache_key = _build_crawl_cache_key(url, target_urls)
        entry = _SCRAPE_CACHE.get(cache_key)

        # Only allow fuzzy domain lookup if target_urls was not explicitly requested
        if entry is None and target_urls is None:
            prefix = f"{host}::"
            for k, e in list(_SCRAPE_CACHE.items()):
                if k == host or k.startswith(prefix):
                    entry = e
                    cache_key = k
                    break

        if entry is None:
            return None
        ttl_hours = 24.0
        try:
            ttl_hours = float(getattr(settings, "SCRAPE_CACHE_TTL_HOURS", 24))
        except (TypeError, ValueError):
            ttl_hours = 24.0
        ttl_seconds = ttl_hours * 3600.0
        if (time.time() - entry.cached_at) > ttl_seconds:
            _SCRAPE_CACHE.pop(cache_key, None)
            return None
        logger.info(
            "[SCRAPE_CACHE] Cache hit for '%s' (cached %.1f hours ago, %d pages). Reusing cached crawl result.",
            cache_key,
            (time.time() - entry.cached_at) / 3600.0,
            len(entry.result.scraped_pages),
        )
        return entry.result
    except Exception as exc:
        logger.debug("Scrape cache lookup error: %s", exc)
        return None


def set_cached_crawl(url: str, result: WebsiteCrawlResult, target_urls: Sequence[str] | None = None) -> None:
    """Cache successful WebsiteCrawlResult for the domain and requested URLs."""
    try:
        if not _is_scrape_cache_enabled():
            return
        if not result or not result.has_usable_content or result.status != "success":
            return
        if not result.scraped_pages:
            return

        # Do not cache runs dominated by inadequate or extremely sparse pages (< MIN_PAGE_ADEQUACY_CHARS)
        min_chars = getattr(settings, "MIN_PAGE_ADEQUACY_CHARS", 50)
        usable_pages = [p for p in result.scraped_pages if p.content and len(p.content.strip()) >= min_chars]
        if not usable_pages:
            logger.info(
                "[SCRAPE_CACHE] Not caching crawl for '%s': inadequate content quality (0 pages with >= %d chars).",
                url,
                min_chars,
            )
            return

        cache_key = _build_crawl_cache_key(url, target_urls or [p.url for p in result.scraped_pages])
        if cache_key:
            _SCRAPE_CACHE[cache_key] = _ScrapeCacheEntry(cached_at=time.time(), result=result)
            logger.info("[SCRAPE_CACHE] Cached successful crawl for '%s' (%d pages).", cache_key, len(result.scraped_pages))
    except Exception as exc:
        logger.debug("Scrape cache store error: %s", exc)


def clear_scrape_cache() -> None:
    """Clear in-memory scrape cache (used in tests or fresh resets)."""
    _SCRAPE_CACHE.clear()


def _clean_html_fragment(soup: BeautifulSoup) -> None:
    """Clean unwanted elements while preserving promotional text, hero sections, and content-rich forms.

    Preserves:
    - Promotional announcement bars, discount codes, and shipping offers.
    - Hero-section headlines, descriptions, and calls to action inside header or sections.
    - Promotional aside callouts and trust guarantees.
    - Substantive content forms (add-to-cart, bookings, calculators).

    Excludes:
    - Scripts, styles, noscript, svg, iframe, canvas.
    - Navigation containers (<nav>, role="navigation", header navigation menus, link lists).
    - Slider/carousel cloned duplicate elements.
    - Cookie banners and notices.
    - Search and newsletter subscription forms.
    - Non-promotional sidebars and footer boilerplate.
    """
    # 1. Technical noise elements
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "canvas"]):
        tag.decompose()

    # 2. Cloned carousel slides and cookie banner elements
    for elem in soup.find_all(True):
        if elem.decomposed:
            continue
        classes = " ".join(elem.get("class") or []).lower()
        elem_id = (elem.get("id") or "").lower()
        if _SLIDER_CLONE_CLASS_REGEX.search(classes) or elem.get("data-clone") is not None:
            elem.decompose()
            continue
        if "cookie-banner" in classes or "cookie-notice" in elem_id or "cookie-banner" in elem_id:
            elem.decompose()
            continue

    # 3. Pure navigation tags and roles
    for nav in soup.find_all(["nav"]):
        nav.decompose()

    for nav_role in soup.find_all(attrs={"role": re.compile(r"^(navigation|menubar)$", re.IGNORECASE)}):
        nav_role.decompose()

    # 4. Header elements: preserve promotional banners and hero content, filter navigation menus
    for header in soup.find_all("header"):
        for child in list(header.find_all(True)):
            if child.decomposed:
                continue
            child_classes = " ".join(child.get("class") or []).lower()
            child_id = (child.get("id") or "").lower()
            is_promo = (
                bool(_PROMOTIONAL_CONTAINER_REGEX.search(child_classes))
                or bool(_PROMOTIONAL_CONTAINER_REGEX.search(child_id))
                or bool(_PROMOTIONAL_TEXT_REGEX.search(child.get_text()))
            )
            if not is_promo:
                if _NAVIGATION_CONTAINER_REGEX.search(child_classes) or _NAVIGATION_CONTAINER_REGEX.search(child_id):
                    child.decompose()
                    continue
                # Decompose link lists in header with links and no promo text
                if child.name in ("ul", "ol") and child.find_all("a"):
                    child.decompose()
                    continue

        # Deduplicate sibling children inside promotional/announcement containers in header
        for promo_container in header.find_all(True):
            if promo_container.decomposed:
                continue
            c_classes = " ".join(promo_container.get("class") or []).lower()
            c_id = (promo_container.get("id") or "").lower()
            if _PROMOTIONAL_CONTAINER_REGEX.search(c_classes) or _PROMOTIONAL_CONTAINER_REGEX.search(c_id):
                seen_texts: set[str] = set()
                for sub in list(promo_container.children):
                    if hasattr(sub, "get_text"):
                        sub_text = sub.get_text().strip()
                        if sub_text:
                            if sub_text in seen_texts:
                                sub.decompose()
                            else:
                                seen_texts.add(sub_text)

        header.unwrap()

    # 5. Aside elements: preserve promotional callouts/banners, remove sidebars/ad noise
    for aside in soup.find_all("aside"):
        aside_classes = " ".join(aside.get("class") or []).lower()
        aside_id = (aside.get("id") or "").lower()
        aside_text = aside.get_text().strip()

        is_promo = (
            bool(_PROMOTIONAL_CONTAINER_REGEX.search(aside_classes))
            or bool(_PROMOTIONAL_CONTAINER_REGEX.search(aside_id))
            or bool(_PROMOTIONAL_TEXT_REGEX.search(aside_text))
        )

        if is_promo:
            for nav in aside.find_all(["nav"]):
                nav.decompose()
            for nav_role in aside.find_all(attrs={"role": re.compile(r"^(navigation|menubar)$", re.IGNORECASE)}):
                nav_role.decompose()
            aside.unwrap()
        else:
            aside.decompose()

    # 6. Footer elements
    for footer in soup.find_all("footer"):
        footer.decompose()

    # 7. Form cleaning: preserve substantive commerce/booking forms, remove search/newsletter noise
    for form in soup.find_all("form"):
        role = (form.get("role") or "").lower()
        classes = " ".join(form.get("class") or []).lower()
        form_id = (form.get("id") or "").lower()
        is_noise = (
            role == "search"
            or "search" in classes
            or "search" in form_id
            or "newsletter" in classes
            or "newsletter" in form_id
            or "subscribe" in classes
            or "subscribe" in form_id
        )
        if is_noise:
            form.decompose()
        else:
            for inp in form.find_all("input"):
                if (inp.get("type") or "").lower() in ("hidden", "password", "submit", "button", "reset"):
                    inp.decompose()
            form.unwrap()


def _extract_text_from_html(html_str: str) -> str:
    """Extract clean readable text from HTML markup, discarding scripts/styles/noise.

    Preserves:
    - Promotional announcement bars, discount codes, shipping offers.
    - Hero-section headlines, descriptions, and calls to action.
    - Product offerings, pricing, and trust messages.

    Excludes:
    - Scripts, styles, noscript, svg, iframe.
    - Navigation menus (<nav>, role="navigation", menu lists).
    - Search and newsletter forms.
    - Footer boilerplate and cookie banners.
    - Duplicate slider/marquee clone lines.
    """
    if not html_str or not html_str.strip():
        return ""
    try:
        soup = BeautifulSoup(html_str, "html.parser")
        _clean_html_fragment(soup)
        raw_text = soup.get_text(separator="\n")

        # Clean lines and remove consecutive duplicate lines (e.g. from ticker/marquee/slider duplicates)
        lines = [line.strip() for line in raw_text.splitlines()]
        deduped_lines: list[str] = []
        for line in lines:
            if not line:
                if deduped_lines and deduped_lines[-1] != "":
                    deduped_lines.append("")
                continue
            if deduped_lines and deduped_lines[-1] == line:
                continue
            deduped_lines.append(line)

        cleaned_text = "\n".join(deduped_lines).strip()
        cleaned_text = clean_boilerplate(cleaned_text)

        min_fallback = 500
        try:
            min_fallback = int(getattr(settings, "MIN_FALLBACK_EXTRACT_CHARS", 500))
        except (TypeError, ValueError):
            min_fallback = 500

        if len(cleaned_text) < min_fallback:
            fallback = extract_structured_fallback_text(html_str)
            if fallback:
                if cleaned_text:
                    return f"{cleaned_text}\n\n{fallback}".strip()
                return fallback.strip()

        return cleaned_text
    except Exception:
        return ""


def _extract_best_content(item: dict[str, Any]) -> str:
    """Select the highest quality content representation from crawler dataset item.

    Prefers clean Markdown if it contains sufficient content (>= MIN_FALLBACK_EXTRACT_CHARS).
    Cleans boilerplate, and if content is sparse, falls back to structured HTML extraction.
    """
    markdown = clean_boilerplate((item.get("markdown") or "").strip())
    text = clean_boilerplate((item.get("text") or "").strip())
    content = clean_boilerplate((item.get("content") or "").strip())
    html = item.get("html") or ""

    min_fallback = 500
    try:
        min_fallback = int(getattr(settings, "MIN_FALLBACK_EXTRACT_CHARS", 500))
    except (TypeError, ValueError):
        min_fallback = 500

    # 1. Preferred representation: markdown with sufficient length
    if len(markdown) >= min_fallback:
        return markdown

    # 2. Candidate from direct text or content fields
    candidate = markdown or text or content

    # 3. If candidate is too short (< min_fallback), check if HTML provides fuller content
    if html:
        html_text = _extract_text_from_html(html).strip()
        if len(html_text) >= min_fallback and len(html_text) > len(candidate):
            return html_text
        if len(candidate) < MIN_PAGE_CONTENT_CHARS and html_text:
            return html_text

    # 4. Fallback to candidate if present, else whatever HTML text was recovered
    if candidate:
        return candidate
    if html:
        return _extract_text_from_html(html).strip()

    return ""


def _is_blocked_content(text: str) -> bool:
    """Determine whether page text signals an active CAPTCHA, WAF, or access block.

    Avoids false positives on substantive pages that merely discuss security,
    include reCAPTCHA badges in footers, or specify access restrictions in terms.
    """
    if not text:
        return False
    lower = text.lower()

    # 1. Definitive challenge signatures are active blocks regardless of length
    if any(sig in lower for sig in DEFINITIVE_BLOCK_SIGNALS):
        return True

    # 2. Ambiguous signals only count if the page is short (< MAX_CHALLENGE_PAGE_CHARS)
    if len(text.strip()) < MAX_CHALLENGE_PAGE_CHARS:
        return any(sig in lower for sig in AMBIGUOUS_BLOCK_SIGNALS)

    return False


def _deduplicate_paragraphs_conservatively(text: str) -> str:
    """Conservatively remove exact duplicate boilerplate paragraphs within a single page.

    Preserves:
    - Short headings, labels, and standalone price/status lines (< 40 chars).
    - Product descriptions, variants, and service outlines.
    - Repeated price lines associated with distinct products.

    Removes:
    - Exact repeated boilerplate paragraphs (>= 40 chars) occurring multiple times on the same page.
    """
    if not text:
        return ""
    paragraphs = text.split("\n\n")
    if len(paragraphs) <= 1:
        return text

    seen_paragraphs: set[str] = set()
    cleaned_paragraphs: list[str] = []

    for p in paragraphs:
        stripped = p.strip()
        if not stripped:
            continue

        # Short lines (< 40 chars) like headings ('Features') or standalone price tags
        # ('Price: $25.00') must never be deduplicated so product associations survive.
        if len(stripped) < 40:
            cleaned_paragraphs.append(stripped)
            continue

        # Substantive boilerplate (>= 40 chars): deduplicate exact identical matches
        normalized_key = re.sub(r"\s+", " ", stripped.lower())
        if normalized_key in seen_paragraphs:
            continue

        seen_paragraphs.add(normalized_key)
        cleaned_paragraphs.append(stripped)

    return "\n\n".join(cleaned_paragraphs)


def _clean_page_content(raw_text: str) -> tuple[str, bool, bool]:
    """Clean extracted page text/markdown to remove boilerplate and noise.

    Returns:
        (cleaned_text, is_blocked, is_truncated)
    """
    if not raw_text:
        return "", False, False

    text = raw_text.strip()

    # 1. Check for blocking / bot detection patterns (with false-positive protection)
    if _is_blocked_content(text):
        return "", True, False

    # 2. Strip leftover HTML tags if present in markdown/text while preserving content-rich forms
    if "<html" in text.lower() or "<body" in text.lower() or "<div" in text.lower() or "<form" in text.lower():
        try:
            soup = BeautifulSoup(text, "html.parser")
            _clean_html_fragment(soup)
            text = soup.get_text(separator="\n")
        except Exception:
            pass

    # 3. Strip cookie banner boilerplate safely without deleting surrounding text
    text = _COOKIE_BANNER_REGEX.sub("", text)

    # 4. Normalize multiple spaces and blank lines
    text = _MULTIPLE_SPACES_REGEX.sub(" ", text)
    text = _MULTIPLE_NEWLINES_REGEX.sub("\n\n", text).strip()

    # 5. Apply conservative duplicate paragraph removal within the page
    text = _deduplicate_paragraphs_conservatively(text)

    # 6. Check length limits
    is_truncated = False
    if len(text) > MAX_PAGE_CONTENT_CHARS:
        text = text[:MAX_PAGE_CONTENT_CHARS].rsplit("\n", 1)[0].strip()
        is_truncated = True

    return text, False, is_truncated


def _build_actor_input(
    urls: list[str],
    *,
    timeout_seconds: int = DEFAULT_ACTOR_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Construct the Apify Website Content Crawler input payload.

    Crucially configures ``maxCrawlDepth=0`` to ensure ONLY the requested startUrls
    are scraped, and no child links or site crawling occurs.
    """
    page_timeout = 25
    try:
        page_timeout = int(getattr(settings, "CRAWLER_PAGE_LOAD_TIMEOUT_SECS", 25))
    except (TypeError, ValueError):
        page_timeout = 25

    nav_timeout = 25
    try:
        nav_timeout = int(getattr(settings, "CRAWLER_NAVIGATION_TIMEOUT_SECS", 25))
    except (TypeError, ValueError):
        nav_timeout = 25

    max_retries = 1
    try:
        max_retries = int(getattr(settings, "CRAWLER_MAX_REQUEST_RETRIES", 1))
    except (TypeError, ValueError):
        max_retries = 1

    max_actor_concurrency = 6
    try:
        max_actor_concurrency = int(getattr(settings, "CRAWLER_MAX_CONCURRENCY_ACTOR", 6))
    except (TypeError, ValueError):
        max_actor_concurrency = 6

    # When direct HTTP is already known to be blocked for this domain, skip adaptive HTTP probe
    # and use 'playwright:chrome' directly to avoid doubling per-page latency.
    crawler_type = "playwright:adaptive"
    browser_only_on_block = getattr(settings, "CRAWLER_BROWSER_ONLY_ON_HTTP_BLOCK", True)
    if browser_only_on_block and urls and any(is_domain_http_blocked(u) for u in urls):
        crawler_type = "playwright:chrome"
        logger.info("[CRAWLER_CONFIG] Domain is HTTP-blocked; configuring Actor with 'playwright:chrome' directly.")

    return {
        "startUrls": [{"url": u} for u in urls],
        # Depth 0 guarantees only startUrls are scraped; no child links are followed.
        "maxCrawlDepth": 0,
        "maxCrawlPages": len(urls),
        "maxConcurrency": min(len(urls), max_actor_concurrency),
        "crawlerType": crawler_type,
        "saveMarkdown": True,
        "saveHtml": False,
        "saveScreenshots": False,
        "removeElementsCssSelector": NOISE_REMOVAL_CSS_SELECTOR,
        "proxyConfiguration": {"useApifyProxy": True},
        "maxRequestRetries": max_retries,
        "pageLoadTimeoutSecs": page_timeout,
        "navigationTimeoutSecs": nav_timeout,
        "waitUntil": "domcontentloaded",
    }


def _run_actor_sync(
    token: str,
    actor_input: dict[str, Any],
    timeout_seconds: int,
) -> tuple[list[dict[str, Any]], str | None, str | None, str | None]:
    """Synchronous worker that calls the Apify Actor via ApifyClient.

    Executed in a separate worker thread via ``asyncio.to_thread`` to prevent
    blocking the FastAPI asynchronous event loop.

    Returns:
        (dataset_items, run_id, error_or_status_message, actor_status)
    """
    client = ApifyClient(token=token)
    run_id: str | None = None
    actor_status: str | None = None
    start_time = time.perf_counter()
    deadline = start_time + timeout_seconds

    try:
        start_urls = [item.get("url") for item in actor_input.get("startUrls", [])]
        logger.info(
            "Starting Apify Actor '%s' for %d URLs (maxCrawlDepth=0).",
            WEBSITE_CONTENT_CRAWLER_ACTOR_ID,
            len(start_urls),
        )

        timeout_delta = datetime.timedelta(seconds=timeout_seconds)
        run = client.actor(WEBSITE_CONTENT_CRAWLER_ACTOR_ID).call(
            run_input=actor_input,
            run_timeout=timeout_delta,
            wait_duration=timeout_delta,
        )

        if run is None:
            return [], None, "Actor.call() returned None; run did not start.", None

        run_id = getattr(run, "id", None) or (run.get("id") if isinstance(run, dict) else None)
        actor_status = getattr(run, "status", None) or (run.get("status") if isinstance(run, dict) else None)
        status_message = (
            getattr(run, "status_message", None)
            or (run.get("statusMessage") or run.get("status_message") if isinstance(run, dict) else "")
            or ""
        )

        logger.info("Actor run '%s' initial status: '%s'.", run_id, actor_status)

        # 1. Handle nonterminal status: if run has not reached a terminal status, wait boundedly
        if actor_status not in TERMINAL_STATUSES and run_id:
            logger.info(
                "Actor run '%s' returned with nonterminal status '%s'; waiting for terminal status...",
                run_id,
                actor_status,
            )
            remaining = deadline - time.perf_counter()
            if remaining > 0:
                try:
                    wait_delta = datetime.timedelta(seconds=max(1.0, remaining))
                    run_client = client.run(run_id)
                    updated_run = run_client.wait_for_finish(wait_duration=wait_delta)
                    if updated_run is not None:
                        run = updated_run
                        actor_status = getattr(run, "status", None) or (run.get("status") if isinstance(run, dict) else None)
                        status_message = (
                            getattr(run, "status_message", None)
                            or (run.get("statusMessage") or run.get("status_message") if isinstance(run, dict) else "")
                            or status_message
                        )
                        logger.info("Actor run '%s' after waiting reached status '%s'.", run_id, actor_status)
                except Exception as wait_exc:
                    logger.warning("Error waiting for Actor run '%s' to finish: %s", run_id, wait_exc)

        # If still nonterminal after waiting (deadline expired while still running/ready)
        if actor_status not in TERMINAL_STATUSES:
            err_msg = (
                f"Actor run '{run_id}' remained nonterminal ('{actor_status}') when the operation deadline ({timeout_seconds}s) expired."
            )
            logger.warning(err_msg)
            dataset_id = (
                getattr(run, "default_dataset_id", None)
                or (run.get("defaultDatasetId") or run.get("default_dataset_id") if isinstance(run, dict) else None)
            )
            if dataset_id:
                try:
                    partial_items = list(client.dataset(dataset_id).iterate_items())
                    if partial_items and any(isinstance(it, dict) for it in partial_items):
                        logger.info("Recovered %d partial items from nonterminal run '%s'.", len(partial_items), run_id)
                        return partial_items, run_id, err_msg, actor_status
                except Exception as rec_exc:
                    logger.debug("Could not recover partial items from dataset '%s': %s", dataset_id, rec_exc)
            return [], run_id, err_msg, actor_status

        # 2. Terminal status reached: retrieve dataset items
        dataset_id = (
            getattr(run, "default_dataset_id", None)
            or (run.get("defaultDatasetId") or run.get("default_dataset_id") if isinstance(run, dict) else None)
        )

        items: list[dict[str, Any]] = []
        dataset_error: str | None = None

        if dataset_id:
            try:
                items = list(client.dataset(dataset_id).iterate_items())
                logger.info(
                    "Actor run '%s': retrieved %d dataset items from dataset '%s' (status: '%s').",
                    run_id,
                    len(items),
                    dataset_id,
                    actor_status,
                )
            except Exception as ds_exc:
                dataset_error = f"Failed to retrieve items from dataset '{dataset_id}': {ds_exc}"
                logger.warning(
                    "Actor run '%s': %s",
                    run_id,
                    dataset_error,
                )
                items = []
        else:
            logger.warning("Actor run '%s' (status '%s') has no default dataset ID in metadata.", run_id, actor_status)

        # 3. Formulate outcome distinguishing actor status from dataset outcome
        if actor_status == "SUCCEEDED":
            if dataset_error:
                return [], run_id, f"Actor run succeeded, but dataset retrieval failed: {dataset_error}", actor_status
            if not dataset_id:
                return [], run_id, "Actor run succeeded, but returned no default dataset ID in metadata.", actor_status
            if not items:
                return [], run_id, f"Actor run succeeded, but default dataset '{dataset_id}' was empty.", actor_status
            return items, run_id, None, actor_status

        if actor_status == "TIMED-OUT":
            base_msg = f"Actor run ended with status 'TIMED-OUT'" + (f": {status_message}" if status_message else ".")
            if dataset_error:
                return [], run_id, f"{base_msg} Dataset retrieval failed: {dataset_error}", actor_status
            if not dataset_id:
                return [], run_id, f"{base_msg} No default dataset ID was returned in actor metadata.", actor_status
            if not items:
                return [], run_id, f"{base_msg} No dataset items were recovered from dataset '{dataset_id}'.", actor_status
            # Partial results recovered
            diag_msg = f"{base_msg} ({len(items)} partial items recovered from dataset '{dataset_id}')."
            return items, run_id, diag_msg, actor_status

        # actor_status in ("FAILED", "ABORTED") or other terminal status
        base_msg = f"Actor run ended with status '{actor_status}'" + (f": {status_message}" if status_message else ".")
        if dataset_error:
            return [], run_id, f"{base_msg} Dataset retrieval failed: {dataset_error}", actor_status
        if items:
            diag_msg = f"{base_msg} ({len(items)} partial items recovered from dataset '{dataset_id}')."
            return items, run_id, diag_msg, actor_status
        return [], run_id, base_msg, actor_status

    except ApifyApiError as exc:
        status_code = getattr(exc, "status_code", None)
        msg = getattr(exc, "message", None) or str(exc)
        if status_code in (401, 403):
            return [], run_id, "Apify authentication failed (HTTP 401/403). Check APIFY_API_TOKEN.", actor_status
        if status_code == 404:
            return [], run_id, f"Apify Actor '{WEBSITE_CONTENT_CRAWLER_ACTOR_ID}' not found (HTTP 404).", actor_status
        return [], run_id, f"Apify API error (HTTP {status_code}): {msg}", actor_status

    except Exception as exc:
        logger.error("Unexpected error invoking Apify Actor '%s': %s", WEBSITE_CONTENT_CRAWLER_ACTOR_ID, exc)
        return [], run_id, f"Crawler invocation failed: {exc}", actor_status


def _normalize_input_pages(
    pages: Sequence[str | SelectedPage] | PageSelectionResult | WebsitePipelineResult,
    max_pages: int,
) -> tuple[dict[str, tuple[str, PageType, str | None]], list[FailedPageScrape]]:
    """Normalize input pages, filter social links, deduplicate, and enforce max_pages limit.

    Returns:
        (valid_targets_dict, immediate_failures)
        - valid_targets_dict: {norm_url: (original_url, page_type, title)}
        - immediate_failures: list of FailedPageScrape for social links or invalid URLs
    """
    raw_candidates: list[tuple[str, PageType, str | None]] = []

    # 1. Unpack various input formats
    if isinstance(pages, WebsitePipelineResult):
        for page in pages.selection_result.selected_pages:
            title_hint = getattr(page, "title", None) or getattr(page, "link_text", None) or None
            raw_candidates.append((page.url, page.page_type, title_hint))
        if not raw_candidates:
            for url in pages.final_urls:
                raw_candidates.append((url, classify_page_type(url), None))
    elif isinstance(pages, PageSelectionResult):
        for page in pages.selected_pages:
            title_hint = getattr(page, "title", None) or getattr(page, "link_text", None) or None
            raw_candidates.append((page.url, page.page_type, title_hint))
    elif isinstance(pages, Sequence):
        for item in pages:
            if isinstance(item, SelectedPage):
                title_hint = getattr(item, "title", None) or getattr(item, "link_text", None) or None
                raw_candidates.append((item.url, item.page_type, title_hint))
            elif isinstance(item, str):
                raw_candidates.append((item, classify_page_type(item), None))

    valid_targets: dict[str, tuple[str, PageType, str | None]] = {}
    failures: list[FailedPageScrape] = []

    for orig_url, p_type, title in raw_candidates:
        norm = _normalize_target_url(orig_url)
        if not norm:
            failures.append(
                FailedPageScrape(
                    url=orig_url,
                    error_message="Invalid or non-HTTP URL format.",
                    page_type=p_type,
                )
            )
            continue

        if _is_social_media_url(norm):
            logger.info("Skipping social media URL from website content crawl: %s", orig_url)
            failures.append(
                FailedPageScrape(
                    url=orig_url,
                    error_message="Skipped social media URL (not allowed in website crawler).",
                    page_type=p_type,
                )
            )
            continue

        # Deduplicate without merging content
        if norm not in valid_targets:
            if len(valid_targets) < max_pages:
                valid_targets[norm] = (orig_url, p_type, title)
            else:
                logger.debug("Page limit of %d reached; skipping candidate %s", max_pages, orig_url)

    return valid_targets, failures


async def _fetch_single_page_http(
    client: httpx.AsyncClient,
    orig_url: str,
    p_type: PageType,
    prior_title: str | None,
) -> tuple[bool, ScrapedPage | None, _PageDiagnostic, str]:
    """Fetch a single page directly via HTTP and validate adequacy.

    Returns:
        (is_success, scraped_page, diagnostic, fallback_reason)
    """
    t_start = time.perf_counter()
    try:
        resp = await client.get(orig_url)
        elapsed = round(time.perf_counter() - t_start, 2)
        status_code = resp.status_code
        final_url = str(resp.url)
        html_content = resp.text or ""

        if status_code != 200:
            log_http_response_diagnostics(
                resp,
                stage="internal_page",
                duration_seconds=elapsed,
                logger_instance=logger,
            )
            cat, reason = _categorize_error(f"HTTP {status_code}", status_code)
            diag = _PageDiagnostic(
                requested_url=orig_url,
                final_url=final_url,
                status="FAILED",
                http_status=status_code,
                duration_seconds=elapsed,
                char_count=0,
                is_truncated=False,
                error_category=cat,
                error_reason=reason,
                retrieval_method="http",
            )
            return False, None, diag, f"http_status_{status_code}"

        if not html_content or _is_blocked_content(html_content) or is_blocked_content(html_content):
            diag = _PageDiagnostic(
                requested_url=orig_url,
                final_url=final_url,
                status="FAILED",
                http_status=status_code,
                duration_seconds=elapsed,
                char_count=0,
                is_truncated=False,
                error_category="ACCESS_DENIED",
                error_reason="Bot challenge or access denial detected via HTTP",
                retrieval_method="http",
            )
            return False, None, diag, "waf_or_bot_challenge"

        clean_text = _extract_text_from_html(html_content)
        cleaned_text, is_blocked, is_truncated = _clean_page_content(clean_text)

        if is_blocked:
            diag = _PageDiagnostic(
                requested_url=orig_url,
                final_url=final_url,
                status="FAILED",
                http_status=status_code,
                duration_seconds=elapsed,
                char_count=len(cleaned_text),
                is_truncated=False,
                error_category="ACCESS_DENIED",
                error_reason="Bot challenge detected in content",
                retrieval_method="http",
            )
            return False, None, diag, "access_denied_in_content"

        adequate, reason = is_content_adequate(
            cleaned_text,
            url=final_url,
            page_type=p_type,
            min_chars=settings.MIN_PAGE_ADEQUACY_CHARS,
        )
        if not adequate:
            cat, cat_reason = _categorize_error(reason, status_code)
            diag = _PageDiagnostic(
                requested_url=orig_url,
                final_url=final_url,
                status="FAILED",
                http_status=status_code,
                duration_seconds=elapsed,
                char_count=len(cleaned_text),
                is_truncated=False,
                error_category=cat,
                error_reason=cat_reason,
                retrieval_method="http",
            )
            return False, None, diag, reason

        extracted_title = prior_title
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            title_elem = soup.find("title")
            if title_elem and title_elem.get_text(strip=True):
                extracted_title = title_elem.get_text(strip=True)
        except Exception:
            pass

        scraped_page = ScrapedPage(
            url=orig_url,
            title=extracted_title,
            content=cleaned_text,
            page_type=p_type,
            status_code=status_code,
            metadata={
                "requested_url": orig_url,
                "final_url": final_url,
                "char_count": len(cleaned_text),
                "is_truncated": is_truncated,
                "duration_seconds": elapsed,
                "retrieval_method": "http",
                "actor_id": None,
            },
        )
        diag = _PageDiagnostic(
            requested_url=orig_url,
            final_url=final_url,
            status="SUCCESS",
            http_status=status_code,
            duration_seconds=elapsed,
            char_count=len(cleaned_text),
            is_truncated=is_truncated,
            retrieval_method="http",
        )
        return True, scraped_page, diag, ""

    except Exception as exc:
        elapsed = round(time.perf_counter() - t_start, 2)
        cat, reason = _categorize_error(str(exc))
        diag = _PageDiagnostic(
            requested_url=orig_url,
            final_url=orig_url,
            status="FAILED",
            http_status=None,
            duration_seconds=elapsed,
            char_count=0,
            is_truncated=False,
            error_category=cat,
            error_reason=f"{type(exc).__name__}: {exc}",
            retrieval_method="http",
        )
        return False, None, diag, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Public Service API
# ---------------------------------------------------------------------------

async def crawl_selected_pages(
    pages: Sequence[str | SelectedPage] | PageSelectionResult | WebsitePipelineResult,
    *,
    website_url: str | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    timeout_seconds: int = DEFAULT_ACTOR_TIMEOUT_SECONDS,
) -> WebsiteCrawlResult:
    """Scrape the readable content of pages selected during page selection.

    Uses the Apify Website Content Crawler in zero-depth mode so that only
    the submitted URLs are fetched, without discovering or crawling extra pages.

    Args:
        pages: The target pages to scrape. Accepts a list of URL strings,
            :class:`SelectedPage` models, a :class:`PageSelectionResult`,
            or a :class:`WebsitePipelineResult`.
        website_url: Optional base website URL. Inferred from input if omitted.
        max_pages: Maximum number of pages to scrape (defaults to 6).
        timeout_seconds: Maximum wall-clock time in seconds for the crawl operation.

    Returns:
        A populated :class:`WebsiteCrawlResult` containing scraped pages and
        failure details.
    """
    start_time = time.perf_counter()

    # Determine fallback website_url from input if not explicitly provided
    resolved_website_url = website_url or ""
    if not resolved_website_url and isinstance(pages, WebsitePipelineResult):
        resolved_website_url = pages.website_url or pages.base_url

    # 1. Normalize and deduplicate target pages
    valid_targets, immediate_failures = _normalize_input_pages(pages, max_pages=max_pages)

    if not resolved_website_url and valid_targets:
        first_norm_url = next(iter(valid_targets.keys()))
        parsed = urllib.parse.urlsplit(first_norm_url)
        resolved_website_url = f"{parsed.scheme}://{parsed.netloc}"

    # Check scrape cache before initiating fresh retrieval
    cached_crawl = get_cached_crawl(resolved_website_url, list(valid_targets.keys()))
    if cached_crawl is not None:
        return cached_crawl

    total_requested = len(valid_targets)

    # 2. Check for empty candidate list
    if total_requested == 0:
        logger.warning("No valid website URLs provided to content crawler.")
        exec_time = round(time.perf_counter() - start_time, 2)
        diag_list = [
            _PageDiagnostic(
                requested_url=f.url,
                final_url=f.url,
                status="FAILED",
                http_status=f.status_code,
                duration_seconds=None,
                char_count=0,
                is_truncated=False,
                error_category=_categorize_error(f.error_message, f.status_code)[0],
                error_reason=_categorize_error(f.error_message, f.status_code)[1],
            )
            for f in immediate_failures
        ]
        _log_crawler_diagnostics(
            website_url=resolved_website_url or "unknown",
            overall_status="empty",
            actor_status=None,
            total_requested=0,
            total_scraped=0,
            total_failed=len(immediate_failures),
            wall_clock_seconds=exec_time,
            diagnostics=diag_list,
            error_message="No valid non-social website URLs submitted for crawling.",
        )
        return WebsiteCrawlResult(
            website_url=resolved_website_url or "unknown",
            status="empty",
            scraped_pages=[],
            failed_pages=immediate_failures,
            total_requested=0,
            total_scraped=0,
            total_failed=len(immediate_failures),
            has_usable_content=False,
            execution_time_seconds=exec_time,
            error_message="No valid non-social website URLs submitted for crawling.",
        )

    # 3. HTTP-First Retrieval with fallback tracking
    http_successes: list[ScrapedPage] = []
    http_diagnostics: list[_PageDiagnostic] = []
    fallback_targets: dict[str, tuple[str, PageType, str | None]] = {}
    http_attempted_count: int = 0
    http_429_count: int = 0

    attempt_http = False
    try:
        attempt_http = bool(getattr(settings, "HTTP_FIRST_RETRIEVAL", True))
    except Exception:
        attempt_http = True

    domain_blocked = is_domain_http_blocked(resolved_website_url)
    if domain_blocked:
        logger.info(
            "[HTTP_FIRST] Domain '%s' was previously blocked (403/429/WAF). Skipping direct HTTP and routing %d page(s) straight to browser fallback.",
            resolved_website_url,
            len(valid_targets),
        )
        fallback_targets = dict(valid_targets)
    elif attempt_http:
        try:
            max_concurrency = max(1, int(getattr(settings, "HTTP_MAX_CONCURRENCY", 2)))
        except (TypeError, ValueError):
            max_concurrency = 2
        logger.info(
            "Attempting HTTP-first retrieval for %d selected pages (max_concurrency=%d)...",
            len(valid_targets),
            max_concurrency,
        )
        http_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        rate_limited_event = asyncio.Event()
        sem = asyncio.Semaphore(max_concurrency)

        async def _bounded_fetch(
            client: httpx.AsyncClient,
            orig_u: str,
            pt: PageType,
            prior_title: str | None,
        ) -> tuple[bool, ScrapedPage | None, _PageDiagnostic, str]:
            nonlocal http_attempted_count, http_429_count

            # 1. Pre-semaphore check: Stop scheduling if crawl is already rate-limited
            if rate_limited_event.is_set():
                diag = _PageDiagnostic(
                    requested_url=orig_u,
                    final_url=orig_u,
                    status="SKIPPED",
                    http_status=None,
                    duration_seconds=0.0,
                    char_count=0,
                    is_truncated=False,
                    error_category="RATE_LIMITED",
                    error_reason="Skipped HTTP attempt: crawl rate-limited by target website (HTTP 429)",
                    retrieval_method="http",
                )
                return False, None, diag, "skipped_due_to_rate_limit"

            # 2. Acquire concurrency semaphore
            async with sem:
                # 3. Post-semaphore check: A concurrent task may have received 429 while this task was waiting
                if rate_limited_event.is_set():
                    diag = _PageDiagnostic(
                        requested_url=orig_u,
                        final_url=orig_u,
                        status="SKIPPED",
                        http_status=None,
                        duration_seconds=0.0,
                        char_count=0,
                        is_truncated=False,
                        error_category="RATE_LIMITED",
                        error_reason="Skipped HTTP attempt: crawl rate-limited by target website (HTTP 429)",
                        retrieval_method="http",
                    )
                    return False, None, diag, "skipped_due_to_rate_limit"

                # 4. Actual HTTP request attempt
                http_attempted_count += 1
                success, page, diag, fb_reason = await _fetch_single_page_http(
                    client, orig_u, pt, prior_title
                )

                # 5. Detect HTTP 429 and activate crawl-scoped stop signal
                if diag and diag.http_status in (401, 403, 429):
                    mark_domain_http_blocked(orig_u, f"internal_http_{diag.http_status}")
                if diag and diag.http_status == 429:
                    http_429_count += 1
                    if not rate_limited_event.is_set():
                        rate_limited_event.set()
                        logger.warning(
                            "[HTTP_FIRST] Website crawl rate-limited (HTTP 429) on '%s'. "
                            "Stopping additional internal HTTP requests for this website crawl.",
                            orig_u,
                        )

                return success, page, diag, fb_reason

        try:
            http_timeout = float(getattr(settings, "HTTP_RETRIEVAL_TIMEOUT_SECONDS", 12.0))
        except (TypeError, ValueError):
            http_timeout = 12.0

        async with httpx.AsyncClient(
            timeout=http_timeout,
            follow_redirects=True,
            headers=http_headers,
            verify=True,
        ) as client:
            tasks = [
                _bounded_fetch(client, orig_u, pt, prior_title)
                for orig_u, pt, prior_title in valid_targets.values()
            ]
            results = await asyncio.gather(*tasks)

        for (norm_u, target_info), (success, page, diag, fb_reason) in zip(valid_targets.items(), results):
            if success and page is not None:
                http_successes.append(page)
                http_diagnostics.append(diag)
                logger.info(
                    "[HTTP_FIRST] Successfully retrieved '%s' via HTTP (%d chars, %.2fs).",
                    target_info[0],
                    len(page.content),
                    diag.duration_seconds or 0.0,
                )
            else:
                fallback_targets[norm_u] = target_info
                http_diagnostics.append(diag)
                logger.info(
                    "[HTTP_FIRST] Page '%s' requires browser fallback (reason: %s).",
                    target_info[0],
                    fb_reason,
                )
    else:
        fallback_targets = dict(valid_targets)

    # If all pages succeeded via HTTP, return immediately without browser crawler
    if not fallback_targets:
        exec_time = round(time.perf_counter() - start_time, 2)
        imm_diags = [
            _PageDiagnostic(
                requested_url=f.url,
                final_url=f.url,
                status="FAILED",
                http_status=f.status_code,
                duration_seconds=None,
                char_count=0,
                is_truncated=False,
                error_category=_categorize_error(f.error_message, f.status_code)[0],
                error_reason=_categorize_error(f.error_message, f.status_code)[1],
                retrieval_method="http",
            )
            for f in immediate_failures
        ]
        all_diags = http_diagnostics + imm_diags
        overall_status: Literal["success", "partial", "empty", "failed"] = (
            "success" if not immediate_failures else "partial"
        )
        _log_crawler_diagnostics(
            website_url=resolved_website_url,
            overall_status=overall_status,
            actor_status=None,
            total_requested=total_requested,
            total_scraped=len(http_successes),
            total_failed=len(immediate_failures),
            wall_clock_seconds=exec_time,
            diagnostics=all_diags,
        )
        logger.info(
            "Website content crawler finished for %s via HTTP: status=%s, scraped=%d/%d, failed=%d (time=%.2fs).",
            resolved_website_url,
            overall_status,
            len(http_successes),
            total_requested,
            len(immediate_failures),
            exec_time,
        )
        http_result = WebsiteCrawlResult(
            website_url=resolved_website_url,
            status=overall_status,
            scraped_pages=http_successes,
            failed_pages=list(immediate_failures),
            total_requested=total_requested,
            total_scraped=len(http_successes),
            total_failed=len(immediate_failures),
            has_usable_content=bool(http_successes),
            actor_run_id=None,
            actor_status=None,
            execution_time_seconds=exec_time,
            http_attempted=http_attempted_count,
            http_succeeded=len(http_successes),
            http_failed=max(0, http_attempted_count - len(http_successes)),
            http_429_count=http_429_count,
            browser_fallback_requested=0,
            browser_fallback_succeeded=0,
        )
        if overall_status == "success" and bool(http_successes):
            set_cached_crawl(resolved_website_url, http_result, list(valid_targets.keys()))
        return http_result

    # 4. Validate Apify API token from settings for fallback targets
    if not settings.APIFY_API_TOKEN:
        logger.error("APIFY_API_TOKEN is not configured in settings.")
        failed_list = [
            FailedPageScrape(url=orig_u, error_message="APIFY_API_TOKEN is not configured.", page_type=pt)
            for orig_u, pt, _ in fallback_targets.values()
        ] + immediate_failures
        exec_time = round(time.perf_counter() - start_time, 2)
        diag_list = http_diagnostics + [
            _PageDiagnostic(
                requested_url=f.url,
                final_url=f.url,
                status="FAILED",
                http_status=f.status_code,
                duration_seconds=None,
                char_count=0,
                is_truncated=False,
                error_category=_categorize_error(f.error_message, f.status_code)[0],
                error_reason=_categorize_error(f.error_message, f.status_code)[1],
                retrieval_method="browser",
            )
            for f in failed_list
        ]
        fb_status: Literal["success", "partial", "empty", "failed"] = "partial" if http_successes else "failed"
        _log_crawler_diagnostics(
            website_url=resolved_website_url,
            overall_status=fb_status,
            actor_status=None,
            total_requested=total_requested,
            total_scraped=len(http_successes),
            total_failed=len(failed_list),
            wall_clock_seconds=exec_time,
            diagnostics=diag_list,
            error_message="APIFY_API_TOKEN is not configured in settings.",
        )
        return WebsiteCrawlResult(
            website_url=resolved_website_url,
            status=fb_status,
            scraped_pages=http_successes,
            failed_pages=failed_list,
            total_requested=total_requested,
            total_scraped=len(http_successes),
            total_failed=len(failed_list),
            has_usable_content=bool(http_successes),
            execution_time_seconds=exec_time,
            error_message="APIFY_API_TOKEN is not configured in settings.",
            http_attempted=http_attempted_count,
            http_succeeded=len(http_successes),
            http_failed=max(0, http_attempted_count - len(http_successes)),
            http_429_count=http_429_count,
            browser_fallback_requested=len(fallback_targets),
            browser_fallback_succeeded=0,
        )

    raw_token: str = settings.APIFY_API_TOKEN.get_secret_value().strip()
    if not raw_token or raw_token in ("your_apify_api_token_here", ""):
        logger.error("APIFY_API_TOKEN is set to a placeholder value.")
        failed_list = [
            FailedPageScrape(url=orig_u, error_message="APIFY_API_TOKEN is a placeholder value.", page_type=pt)
            for orig_u, pt, _ in fallback_targets.values()
        ] + immediate_failures
        exec_time = round(time.perf_counter() - start_time, 2)
        diag_list = http_diagnostics + [
            _PageDiagnostic(
                requested_url=f.url,
                final_url=f.url,
                status="FAILED",
                http_status=f.status_code,
                duration_seconds=None,
                char_count=0,
                is_truncated=False,
                error_category=_categorize_error(f.error_message, f.status_code)[0],
                error_reason=_categorize_error(f.error_message, f.status_code)[1],
                retrieval_method="browser",
            )
            for f in failed_list
        ]
        fb_status = "partial" if http_successes else "failed"
        _log_crawler_diagnostics(
            website_url=resolved_website_url,
            overall_status=fb_status,
            actor_status=None,
            total_requested=total_requested,
            total_scraped=len(http_successes),
            total_failed=len(failed_list),
            wall_clock_seconds=exec_time,
            diagnostics=diag_list,
            error_message="APIFY_API_TOKEN is set to a placeholder value.",
        )
        return WebsiteCrawlResult(
            website_url=resolved_website_url,
            status=fb_status,
            scraped_pages=http_successes,
            failed_pages=failed_list,
            total_requested=total_requested,
            total_scraped=len(http_successes),
            total_failed=len(failed_list),
            has_usable_content=bool(http_successes),
            execution_time_seconds=exec_time,
            error_message="APIFY_API_TOKEN is set to a placeholder value.",
            http_attempted=http_attempted_count,
            http_succeeded=len(http_successes),
            http_failed=max(0, http_attempted_count - len(http_successes)),
            http_429_count=http_429_count,
            browser_fallback_requested=len(fallback_targets),
            browser_fallback_succeeded=0,
        )

    # 5. Prepare Actor input configuration for fallback targets only
    urls_to_scrape = [orig_u for orig_u, _, _ in fallback_targets.values()]
    actor_input = _build_actor_input(urls_to_scrape, timeout_seconds=timeout_seconds)

    # 6. Execute Actor with transient retries
    hard_timeout = timeout_seconds
    try:
        hard_timeout = max(60, min(timeout_seconds, int(getattr(settings, "CRAWLER_HARD_TIMEOUT_SECONDS", 90))))
    except (TypeError, ValueError):
        hard_timeout = timeout_seconds

    items: list[dict[str, Any]] = []
    actor_run_id: str | None = None
    last_error: str | None = None
    actor_final_status: str | None = None

    for attempt in range(1, MAX_TRANSIENT_RETRIES + 1):
        logger.info(
            "Executing website content crawler (attempt %d/%d) for %d URLs (timeout=%ds)...",
            attempt,
            MAX_TRANSIENT_RETRIES,
            len(urls_to_scrape),
            hard_timeout,
        )
        sync_res = await asyncio.to_thread(
            _run_actor_sync,
            raw_token,
            actor_input,
            hard_timeout,
        )
        if len(sync_res) >= 4:
            items, run_id, last_error, actor_final_status = sync_res[:4]
        else:
            items, run_id, last_error = sync_res[:3]
            actor_final_status = "SUCCEEDED" if last_error is None else "FAILED"
        if run_id:
            actor_run_id = run_id

        # If run succeeded, break immediately
        if last_error is None and actor_final_status == "SUCCEEDED":
            break

        # If partial dataset items were recovered, DO NOT retry a full crawl!
        if items:
            logger.info(
                "Recovered %d dataset items from run with status '%s'; avoiding full retry.",
                len(items),
                actor_final_status,
            )
            break

        if actor_final_status in NON_TERMINAL_STATUSES:
            logger.warning(
                "Actor run '%s' remained nonterminal ('%s'); aborting retries without starting duplicate crawl.",
                actor_run_id,
                actor_final_status,
            )
            break

        if actor_final_status == "SUCCEEDED" and last_error:
            logger.warning(
                "Actor run '%s' already completed successfully, but dataset retrieval encountered an error (%s); avoiding duplicate actor run.",
                actor_run_id,
                last_error,
            )
            break

        if actor_final_status in ("TIMED-OUT", "ABORTED"):
            logger.warning(
                "Actor run '%s' ended with terminal status '%s'; aborting retries.",
                actor_run_id,
                actor_final_status,
            )
            break

        if last_error:
            lower_err = last_error.lower()
            if (
                "401" in last_error
                or "403" in last_error
                or "404" in last_error
                or "timed-out" in lower_err
                or "timeout" in lower_err
            ):
                logger.warning(
                    "Non-retryable error ('%s'); aborting retries.",
                    last_error,
                )
                break

        if attempt < MAX_TRANSIENT_RETRIES:
            logger.warning("Transient crawler error on attempt %d: %s. Retrying...", attempt, last_error)
            await asyncio.sleep(2.0 * attempt)

    # If actor execution yielded zero items (total failure, timeout with 0 items, nonterminal, or dataset failure)
    if not items:
        err_msg = last_error or "Actor execution failed to return any dataset items."
        if actor_final_status in NON_TERMINAL_STATUSES:
            page_err = f"Page not scraped: crawler run remained nonterminal ('{actor_final_status}') when the operation deadline expired."
        elif actor_final_status == "TIMED-OUT":
            page_err = "Page not scraped: crawler run timed out before completion."
        elif actor_final_status and actor_final_status != "SUCCEEDED":
            page_err = f"Page not scraped: crawler run ended with status '{actor_final_status}'."
        else:
            page_err = f"Page not scraped: {err_msg}"

        failed_list = [
            FailedPageScrape(url=orig_u, error_message=page_err, page_type=pt)
            for orig_u, pt, _ in fallback_targets.values()
        ] + immediate_failures
        exec_time = round(time.perf_counter() - start_time, 2)
        diag_list = http_diagnostics + [
            _PageDiagnostic(
                requested_url=f.url,
                final_url=f.url,
                status="FAILED",
                http_status=f.status_code,
                duration_seconds=None,
                char_count=0,
                is_truncated=False,
                error_category=_categorize_error(f.error_message, f.status_code)[0],
                error_reason=_categorize_error(f.error_message, f.status_code)[1],
                retrieval_method="browser",
            )
            for f in failed_list
        ]
        overall_status = "partial" if http_successes else "failed"
        _log_crawler_diagnostics(
            website_url=resolved_website_url,
            overall_status=overall_status,
            actor_status=actor_final_status,
            total_requested=total_requested,
            total_scraped=len(http_successes),
            total_failed=len(failed_list),
            wall_clock_seconds=exec_time,
            diagnostics=diag_list,
            actor_run_id=actor_run_id,
            error_message=err_msg,
        )
        return WebsiteCrawlResult(
            website_url=resolved_website_url,
            status=overall_status,
            scraped_pages=http_successes,
            failed_pages=failed_list,
            total_requested=total_requested,
            total_scraped=len(http_successes),
            total_failed=len(failed_list),
            has_usable_content=bool(http_successes),
            actor_run_id=actor_run_id,
            actor_status=actor_final_status,
            execution_time_seconds=exec_time,
            error_message=err_msg,
        )

    # 7. Parse and validate returned dataset items
    browser_scraped_pages: list[ScrapedPage] = []
    browser_failures: list[FailedPageScrape] = []
    processed_targets: set[str] = set()
    browser_diagnostics: list[_PageDiagnostic] = []

    for item in items:
        # Extract returned URL
        item_url = (
            item.get("url")
            or item.get("crawl", {}).get("loadedUrl")
            or item.get("loadedUrl")
            or item.get("finalUrl")
            or ""
        )
        norm_item_url = _normalize_target_url(item_url) if item_url else None

        # Find matching requested target among fallback_targets
        matched_norm: str | None = None
        if norm_item_url and norm_item_url in fallback_targets:
            matched_norm = norm_item_url
        else:
            # Fallback path matching
            for target_norm in fallback_targets:
                if target_norm not in processed_targets:
                    p1 = urllib.parse.urlsplit(target_norm)
                    p2 = urllib.parse.urlsplit(norm_item_url or "")
                    if p1.netloc == p2.netloc and p1.path == p2.path:
                        matched_norm = target_norm
                        break

        if not matched_norm:
            logger.debug("Received dataset item with unrequested URL: %s; skipping.", item_url)
            continue

        if matched_norm in processed_targets:
            logger.debug("Duplicate result received for %s; deduplicating.", matched_norm)
            continue

        processed_targets.add(matched_norm)
        orig_url, p_type, prior_title = fallback_targets[matched_norm]

        # Extract HTTP status code, duration, and redirected final URL
        crawl_obj = item.get("crawl") if isinstance(item.get("crawl"), dict) else {}
        status_code = crawl_obj.get("httpStatusCode") or item.get("httpStatusCode") or item.get("statusCode")
        if status_code is not None:
            try:
                status_code = int(status_code)
            except (ValueError, TypeError):
                status_code = None

        final_url = (
            item.get("loadedUrl")
            or crawl_obj.get("loadedUrl")
            or item.get("finalUrl")
            or item_url
            or orig_url
        )
        item_duration = _extract_item_duration(item)

        # Check HTTP error status
        if status_code is not None and status_code >= 400:
            err_msg = f"HTTP server error status {status_code}."
            browser_failures.append(
                FailedPageScrape(
                    url=orig_url,
                    error_message=err_msg,
                    status_code=status_code,
                    page_type=p_type,
                )
            )
            cat, reason = _categorize_error(err_msg, status_code)
            browser_diagnostics.append(
                _PageDiagnostic(
                    requested_url=orig_url,
                    final_url=final_url,
                    status="FAILED",
                    http_status=status_code,
                    duration_seconds=item_duration,
                    char_count=0,
                    is_truncated=False,
                    error_category=cat,
                    error_reason=reason,
                    retrieval_method="browser",
                )
            )
            continue

        # Extract content using best available representation
        raw_content = _extract_best_content(item)
        cleaned_text, is_blocked, is_truncated = _clean_page_content(raw_content)

        if is_blocked:
            err_msg = "Page access denied or bot challenge detected."
            browser_failures.append(
                FailedPageScrape(
                    url=orig_url,
                    error_message=err_msg,
                    status_code=status_code,
                    page_type=p_type,
                )
            )
            cat, reason = _categorize_error(err_msg, status_code)
            browser_diagnostics.append(
                _PageDiagnostic(
                    requested_url=orig_url,
                    final_url=final_url,
                    status="FAILED",
                    http_status=status_code,
                    duration_seconds=item_duration,
                    char_count=len(cleaned_text),
                    is_truncated=False,
                    error_category=cat,
                    error_reason=reason,
                    retrieval_method="browser",
                )
            )
            continue

        if len(cleaned_text) < MIN_PAGE_CONTENT_CHARS:
            err_msg = f"Extracted content is too short or empty ({len(cleaned_text)} characters)."
            browser_failures.append(
                FailedPageScrape(
                    url=orig_url,
                    error_message=err_msg,
                    status_code=status_code,
                    page_type=p_type,
                )
            )
            cat, reason = _categorize_error(err_msg, status_code)
            browser_diagnostics.append(
                _PageDiagnostic(
                    requested_url=orig_url,
                    final_url=final_url,
                    status="FAILED",
                    http_status=status_code,
                    duration_seconds=item_duration,
                    char_count=len(cleaned_text),
                    is_truncated=False,
                    error_category=cat,
                    error_reason=reason,
                    retrieval_method="browser",
                )
            )
            continue

        # Extract title
        metadata_obj = item.get("metadata") or {}
        extracted_title = (
            item.get("title")
            or metadata_obj.get("title")
            or prior_title
            or None
        )
        if extracted_title:
            extracted_title = extracted_title.strip()

        scraped_page = ScrapedPage(
            url=orig_url,
            title=extracted_title,
            content=cleaned_text,
            page_type=p_type,
            status_code=status_code,
            metadata={
                "requested_url": orig_url,
                "final_url": final_url,
                "char_count": len(cleaned_text),
                "is_truncated": is_truncated,
                "duration_seconds": item_duration,
                "retrieval_method": "browser",
                "actor_id": WEBSITE_CONTENT_CRAWLER_ACTOR_ID,
                "description": metadata_obj.get("description"),
                "language_code": metadata_obj.get("languageCode"),
            },
        )
        browser_scraped_pages.append(scraped_page)
        browser_diagnostics.append(
            _PageDiagnostic(
                requested_url=orig_url,
                final_url=final_url,
                status="SUCCESS",
                http_status=status_code,
                duration_seconds=item_duration,
                char_count=len(cleaned_text),
                is_truncated=is_truncated,
                error_category=None,
                error_reason=None,
                retrieval_method="browser",
            )
        )

    # 8. Identify requested fallback pages that were missing from dataset results
    for norm_u, (orig_u, p_type, _) in fallback_targets.items():
        if norm_u not in processed_targets:
            if actor_final_status == "TIMED-OUT":
                miss_err = "Page not scraped: crawler run timed out before completion."
            elif actor_final_status and actor_final_status != "SUCCEEDED":
                miss_err = f"Page not scraped: crawler run ended with status '{actor_final_status}'."
            else:
                miss_err = "Page was not returned in the crawler dataset output."

            browser_failures.append(
                FailedPageScrape(
                    url=orig_u,
                    error_message=miss_err,
                    page_type=p_type,
                )
            )
            cat, reason = _categorize_error(miss_err, None)
            browser_diagnostics.append(
                _PageDiagnostic(
                    requested_url=orig_u,
                    final_url=orig_u,
                    status="FAILED",
                    http_status=None,
                    duration_seconds=None,
                    char_count=0,
                    is_truncated=False,
                    error_category=cat,
                    error_reason=reason,
                    retrieval_method="browser",
                )
            )

    # 9. Combine HTTP and Browser results
    imm_diags = [
        _PageDiagnostic(
            requested_url=f.url,
            final_url=f.url,
            status="FAILED",
            http_status=f.status_code,
            duration_seconds=None,
            char_count=0,
            is_truncated=False,
            error_category=_categorize_error(f.error_message, f.status_code)[0],
            error_reason=_categorize_error(f.error_message, f.status_code)[1],
            retrieval_method="http",
        )
        for f in immediate_failures
    ]
    scraped_pages = http_successes + browser_scraped_pages
    page_failures = list(immediate_failures) + browser_failures
    diagnostics = http_diagnostics + browser_diagnostics + imm_diags

    total_scraped = len(scraped_pages)
    total_failed = len(page_failures)
    has_usable_content = total_scraped > 0

    if total_scraped == total_requested and actor_final_status == "SUCCEEDED":
        overall_status = "success"
    elif total_scraped > 0:
        overall_status = "partial"
    else:
        overall_status = "empty" if any(f.error_message and "too short" in f.error_message for f in page_failures) else "failed"

    exec_time = round(time.perf_counter() - start_time, 2)

    # Structured diagnostic logs
    _log_crawler_diagnostics(
        website_url=resolved_website_url,
        overall_status=overall_status,
        actor_status=actor_final_status,
        total_requested=total_requested,
        total_scraped=total_scraped,
        total_failed=total_failed,
        wall_clock_seconds=exec_time,
        diagnostics=diagnostics,
        actor_run_id=actor_run_id,
        error_message=last_error,
    )

    logger.info(
        "Website content crawler finished for %s: status=%s (actor_status=%s), "
        "scraped=%d/%d (http=%d, browser=%d), failed=%d (time=%.2fs) | "
        "metrics: http_attempted=%d, http_succeeded=%d, http_failed=%d, http_429=%d, fallback_requested=%d, fallback_succeeded=%d.",
        resolved_website_url,
        overall_status,
        actor_final_status,
        total_scraped,
        total_requested,
        len(http_successes),
        len(browser_scraped_pages),
        total_failed,
        exec_time,
        http_attempted_count,
        len(http_successes),
        max(0, http_attempted_count - len(http_successes)),
        http_429_count,
        len(fallback_targets),
        len(browser_scraped_pages),
    )

    crawl_result = WebsiteCrawlResult(
        website_url=resolved_website_url,
        status=overall_status,
        scraped_pages=scraped_pages,
        failed_pages=page_failures,
        total_requested=total_requested,
        total_scraped=total_scraped,
        total_failed=total_failed,
        has_usable_content=has_usable_content,
        actor_run_id=actor_run_id,
        actor_status=actor_final_status,
        execution_time_seconds=exec_time,
        error_message=last_error,
        http_attempted=http_attempted_count,
        http_succeeded=len(http_successes),
        http_failed=max(0, http_attempted_count - len(http_successes)),
        http_429_count=http_429_count,
        browser_fallback_requested=len(fallback_targets),
        browser_fallback_succeeded=len(browser_scraped_pages),
    )
    if overall_status == "success" and has_usable_content:
        set_cached_crawl(resolved_website_url, crawl_result, list(valid_targets.keys()))
    return crawl_result


# Backward-compatible alias
crawl_website_content = crawl_selected_pages
