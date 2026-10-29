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
import datetime
import logging
import re
import time
import urllib.parse
from typing import Any, Literal, Sequence

from apify_client import ApifyClient
from apify_client.errors import ApifyApiError
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.config import settings
from backend.app.schemas.enums import PageType
from backend.app.schemas.scraped_data import ScrapedPage
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

# Default maximum number of pages to scrape per crawl run
DEFAULT_MAX_PAGES: int = 8

# Maximum runtime allowed for the Apify Actor run
DEFAULT_ACTOR_TIMEOUT_SECONDS: int = 180

# Retries for transient network/platform errors
MAX_TRANSIENT_RETRIES: int = 2

# Minimum character count for content to be considered meaningful
MIN_PAGE_CONTENT_CHARS: int = 50

# Maximum character count to prevent memory bloat or downstream LLM context overflow (~25k tokens)
MAX_PAGE_CONTENT_CHARS: int = 100_000

# CSS selectors for DOM elements removed by the actor prior to content conversion
NOISE_REMOVAL_CSS_SELECTOR: str = (
    "nav, header, footer, script, style, noscript, svg, aside, form, "
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

# Block signals indicating bot detection, CAPTCHA, or WAF challenges
BLOCK_SIGNALS: tuple[str, ...] = (
    "captcha",
    "g-recaptcha",
    "hcaptcha",
    "cf-challenge",
    "cloudflare ray id",
    "access denied",
    "403 forbidden",
    "451 unavailable",
    "you have been blocked",
    "bot detected",
    "please verify you are a human",
    "ddos-guard",
    "security check to continue",
)

# Regex patterns for removing cookie consent and repetitive noise
_COOKIE_BANNER_REGEX = re.compile(
    r"(?i)(?:we use cookies|this website uses cookies|cookie preferences|accept all cookies|privacy choices).*?(?:accept|agree|decline|manage|close)\b",
    re.DOTALL,
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
    execution_time_seconds: float = Field(
        default=0.0,
        description="Total wall-clock duration in seconds.",
    )
    error_message: str | None = Field(
        default=None,
        description="High-level error description if the actor run failed.",
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


def _clean_page_content(raw_text: str) -> tuple[str, bool, bool]:
    """Clean extracted page text/markdown to remove boilerplate and noise.

    Returns:
        (cleaned_text, is_blocked, is_truncated)
    """
    if not raw_text:
        return "", False, False

    text = raw_text.strip()

    # 1. Check for blocking / bot detection patterns
    lower_snippet = text[:2000].lower()
    is_blocked = any(signal in lower_snippet for signal in BLOCK_SIGNALS)
    if is_blocked:
        return "", True, False

    # 2. Strip leftover HTML tags if present in markdown/text
    if "<html" in text.lower() or "<body" in text.lower() or "<div" in text.lower():
        try:
            soup = BeautifulSoup(text, "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "svg", "form"]):
                tag.decompose()
            text = soup.get_text(separator="\n")
        except Exception:
            pass

    # 3. Strip cookie banner boilerplate
    text = _COOKIE_BANNER_REGEX.sub("", text)

    # 4. Normalize multiple spaces and excess blank lines
    text = _MULTIPLE_SPACES_REGEX.sub(" ", text)
    text = _MULTIPLE_NEWLINES_REGEX.sub("\n\n", text).strip()

    # 5. Check length limits
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
    return {
        "startUrls": [{"url": u} for u in urls],
        # Depth 0 guarantees only startUrls are scraped; no child links are followed.
        "maxCrawlDepth": 0,
        "maxCrawlPages": len(urls),
        "maxConcurrency": min(len(urls), 4),
        "crawlerType": "playwright:adaptive",
        "saveMarkdown": True,
        "saveHtml": False,
        "saveScreenshots": False,
        "removeElementsCssSelector": NOISE_REMOVAL_CSS_SELECTOR,
        "proxyConfiguration": {"useApifyProxy": True},
        "maxRequestRetries": 1,
        "pageLoadTimeoutSecs": min(timeout_seconds // 2, 45),
    }


def _run_actor_sync(
    token: str,
    actor_input: dict[str, Any],
    timeout_seconds: int,
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    """Synchronous worker that calls the Apify Actor via ApifyClient.

    Executed in a separate worker thread via ``asyncio.to_thread`` to prevent
    blocking the FastAPI asynchronous event loop.
    """
    client = ApifyClient(token=token)
    run_id: str | None = None

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
            return [], None, "Actor.call() returned None; run did not start."

        run_id = getattr(run, "id", None) or (run.get("id") if isinstance(run, dict) else None)
        run_status = getattr(run, "status", None) or (run.get("status") if isinstance(run, dict) else None)

        logger.info("Actor run '%s' completed with status '%s'.", run_id, run_status)

        if run_status != "SUCCEEDED":
            status_message = (
                getattr(run, "status_message", None)
                or (run.get("statusMessage") or run.get("status_message") if isinstance(run, dict) else "")
                or ""
            )
            return (
                [],
                run_id,
                f"Actor run ended with status '{run_status}'"
                + (f": {status_message}" if status_message else "."),
            )

        dataset_id = (
            getattr(run, "default_dataset_id", None)
            or (run.get("defaultDatasetId") or run.get("default_dataset_id") if isinstance(run, dict) else None)
        )
        if not dataset_id:
            return [], run_id, "Actor run succeeded but returned no default dataset ID."

        items = list(client.dataset(dataset_id).iterate_items())
        logger.info(
            "Actor run '%s': retrieved %d dataset items from dataset '%s'.",
            run_id,
            len(items),
            dataset_id,
        )
        return items, run_id, None

    except ApifyApiError as exc:
        status_code = getattr(exc, "status_code", None)
        msg = getattr(exc, "message", None) or str(exc)
        if status_code in (401, 403):
            return [], run_id, "Apify authentication failed (HTTP 401/403). Check APIFY_API_TOKEN."
        if status_code == 404:
            return [], run_id, f"Apify Actor '{WEBSITE_CONTENT_CRAWLER_ACTOR_ID}' not found (HTTP 404)."
        return [], run_id, f"Apify API error (HTTP {status_code}): {msg}"

    except Exception as exc:
        logger.error("Unexpected error invoking Apify Actor '%s': %s", WEBSITE_CONTENT_CRAWLER_ACTOR_ID, exc)
        return [], run_id, f"Crawler invocation failed: {exc}"


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
        max_pages: Maximum number of pages to scrape (defaults to 8).
        timeout_seconds: Maximum wall-clock time in seconds for the crawl operation.

    Returns:
        A populated :class:`WebsiteCrawlResult` containing scraped pages and
        failure details.
    """
    start_time = time.monotonic()

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

    total_requested = len(valid_targets)

    # 2. Check for empty candidate list
    if total_requested == 0:
        logger.warning("No valid website URLs provided to content crawler.")
        return WebsiteCrawlResult(
            website_url=resolved_website_url or "unknown",
            status="empty",
            scraped_pages=[],
            failed_pages=immediate_failures,
            total_requested=0,
            total_scraped=0,
            total_failed=len(immediate_failures),
            has_usable_content=False,
            execution_time_seconds=round(time.monotonic() - start_time, 2),
            error_message="No valid non-social website URLs submitted for crawling.",
        )

    # 3. Validate Apify API token from settings
    if not settings.APIFY_API_TOKEN:
        logger.error("APIFY_API_TOKEN is not configured in settings.")
        return WebsiteCrawlResult(
            website_url=resolved_website_url,
            status="failed",
            scraped_pages=[],
            failed_pages=[
                FailedPageScrape(url=orig_u, error_message="APIFY_API_TOKEN is not configured.", page_type=pt)
                for orig_u, pt, _ in valid_targets.values()
            ] + immediate_failures,
            total_requested=total_requested,
            total_scraped=0,
            total_failed=total_requested + len(immediate_failures),
            has_usable_content=False,
            execution_time_seconds=round(time.monotonic() - start_time, 2),
            error_message="APIFY_API_TOKEN is not configured in settings.",
        )

    raw_token: str = settings.APIFY_API_TOKEN.get_secret_value().strip()
    if not raw_token or raw_token in ("your_apify_api_token_here", ""):
        logger.error("APIFY_API_TOKEN is set to a placeholder value.")
        return WebsiteCrawlResult(
            website_url=resolved_website_url,
            status="failed",
            scraped_pages=[],
            failed_pages=[
                FailedPageScrape(url=orig_u, error_message="APIFY_API_TOKEN is a placeholder value.", page_type=pt)
                for orig_u, pt, _ in valid_targets.values()
            ] + immediate_failures,
            total_requested=total_requested,
            total_scraped=0,
            total_failed=total_requested + len(immediate_failures),
            has_usable_content=False,
            execution_time_seconds=round(time.monotonic() - start_time, 2),
            error_message="APIFY_API_TOKEN is set to a placeholder value.",
        )

    # 4. Prepare Actor input configuration
    urls_to_scrape = [orig_u for orig_u, _, _ in valid_targets.values()]
    actor_input = _build_actor_input(urls_to_scrape, timeout_seconds=timeout_seconds)

    # 5. Execute Actor with transient retries
    items: list[dict[str, Any]] = []
    actor_run_id: str | None = None
    last_error: str | None = None

    for attempt in range(1, MAX_TRANSIENT_RETRIES + 1):
        logger.info(
            "Executing website content crawler (attempt %d/%d) for %d URLs...",
            attempt,
            MAX_TRANSIENT_RETRIES,
            len(urls_to_scrape),
        )
        items, run_id, last_error = await asyncio.to_thread(
            _run_actor_sync,
            raw_token,
            actor_input,
            timeout_seconds,
        )
        if run_id:
            actor_run_id = run_id

        if last_error is None:
            break

        # Permanent errors or timeout failures should not be retried blindly
        lower_err = last_error.lower()
        if "401" in last_error or "403" in last_error or "404" in last_error or "timed-out" in lower_err or "timeout" in lower_err:
            logger.warning("Non-retryable or timeout error encountered from Apify: %s; aborting retries.", last_error)
            break

        if attempt < MAX_TRANSIENT_RETRIES:
            logger.warning("Transient crawler error on attempt %d: %s. Retrying...", attempt, last_error)
            await asyncio.sleep(2.0 * attempt)

    # If actor execution failed entirely
    if last_error is not None and not items:
        failed_list = [
            FailedPageScrape(url=orig_u, error_message=f"Actor execution failed: {last_error}", page_type=pt)
            for orig_u, pt, _ in valid_targets.values()
        ] + immediate_failures
        return WebsiteCrawlResult(
            website_url=resolved_website_url,
            status="failed",
            scraped_pages=[],
            failed_pages=failed_list,
            total_requested=total_requested,
            total_scraped=0,
            total_failed=len(failed_list),
            has_usable_content=False,
            actor_run_id=actor_run_id,
            execution_time_seconds=round(time.monotonic() - start_time, 2),
            error_message=last_error,
        )

    # 6. Parse and validate returned dataset items
    scraped_pages: list[ScrapedPage] = []
    page_failures: list[FailedPageScrape] = list(immediate_failures)
    processed_targets: set[str] = set()

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

        # Find matching requested target
        matched_norm: str | None = None
        if norm_item_url and norm_item_url in valid_targets:
            matched_norm = norm_item_url
        else:
            # Fallback path matching
            for target_norm in valid_targets:
                if target_norm not in processed_targets:
                    # Compare host and path
                    p1 = urllib.parse.urlsplit(target_norm)
                    p2 = urllib.parse.urlsplit(norm_item_url or "")
                    if p1.netloc == p2.netloc and p1.path == p2.path:
                        matched_norm = target_norm
                        break

        # If item doesn't map to a requested target, ignore it to prevent scope creep
        if not matched_norm:
            logger.debug("Received dataset item with unrequested URL: %s; skipping.", item_url)
            continue

        if matched_norm in processed_targets:
            logger.debug("Duplicate result received for %s; deduplicating.", matched_norm)
            continue

        processed_targets.add(matched_norm)
        orig_url, p_type, prior_title = valid_targets[matched_norm]

        # Extract HTTP status code
        crawl_obj = item.get("crawl") or {}
        status_code = crawl_obj.get("httpStatusCode") or item.get("httpStatusCode")
        if status_code is not None:
            try:
                status_code = int(status_code)
            except (ValueError, TypeError):
                status_code = None

        # Check HTTP error status
        if status_code is not None and status_code >= 400:
            page_failures.append(
                FailedPageScrape(
                    url=orig_url,
                    error_message=f"HTTP server error status {status_code}.",
                    status_code=status_code,
                    page_type=p_type,
                )
            )
            continue

        # Extract content (markdown preferred, then text, then HTML)
        raw_content = item.get("markdown") or item.get("text") or item.get("content") or ""
        if not raw_content and item.get("html"):
            try:
                soup = BeautifulSoup(item["html"], "html.parser")
                for tag in soup(["script", "style", "nav", "header", "footer"]):
                    tag.decompose()
                raw_content = soup.get_text(separator="\n")
            except Exception:
                pass

        cleaned_text, is_blocked, is_truncated = _clean_page_content(raw_content)

        if is_blocked:
            page_failures.append(
                FailedPageScrape(
                    url=orig_url,
                    error_message="Page access denied or bot challenge detected.",
                    status_code=status_code or 403,
                    page_type=p_type,
                )
            )
            continue

        if len(cleaned_text) < MIN_PAGE_CONTENT_CHARS:
            page_failures.append(
                FailedPageScrape(
                    url=orig_url,
                    error_message=f"Extracted content is too short or empty ({len(cleaned_text)} characters).",
                    status_code=status_code,
                    page_type=p_type,
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

        # Build ScrapedPage schema instance
        scraped_page = ScrapedPage(
            url=orig_url,
            title=extracted_title,
            content=cleaned_text,
            page_type=p_type,
            status_code=status_code or 200,
            metadata={
                "requested_url": orig_url,
                "final_url": item_url or orig_url,
                "char_count": len(cleaned_text),
                "is_truncated": is_truncated,
                "actor_id": WEBSITE_CONTENT_CRAWLER_ACTOR_ID,
                "description": metadata_obj.get("description"),
                "language_code": metadata_obj.get("languageCode"),
            },
        )
        scraped_pages.append(scraped_page)

    # 7. Identify requested pages that were missing from dataset results
    for norm_u, (orig_u, p_type, _) in valid_targets.items():
        if norm_u not in processed_targets:
            page_failures.append(
                FailedPageScrape(
                    url=orig_u,
                    error_message="Page was not returned in the crawler dataset output.",
                    page_type=p_type,
                )
            )

    # 8. Determine overall crawl status
    total_scraped = len(scraped_pages)
    total_failed = len(page_failures)
    has_usable_content = total_scraped > 0

    if total_scraped == total_requested:
        overall_status: Literal["success", "partial", "empty", "failed"] = "success"
    elif total_scraped > 0:
        overall_status = "partial"
    else:
        overall_status = "failed"

    exec_time = round(time.monotonic() - start_time, 2)
    logger.info(
        "Website content crawler finished for %s: status=%s, scraped=%d/%d, failed=%d (time=%.2fs).",
        resolved_website_url,
        overall_status,
        total_scraped,
        total_requested,
        total_failed,
        exec_time,
    )

    return WebsiteCrawlResult(
        website_url=resolved_website_url,
        status=overall_status,
        scraped_pages=scraped_pages,
        failed_pages=page_failures,
        total_requested=total_requested,
        total_scraped=total_scraped,
        total_failed=total_failed,
        has_usable_content=has_usable_content,
        actor_run_id=actor_run_id,
        execution_time_seconds=exec_time,
        error_message=last_error,
    )


# Backward-compatible alias
crawl_website_content = crawl_selected_pages
