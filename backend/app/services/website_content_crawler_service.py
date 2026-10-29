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
DEFAULT_MAX_PAGES: int = 10

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


def _clean_html_fragment(soup: BeautifulSoup) -> None:
    """Clean unwanted elements while preserving content-rich forms (products, bookings)."""
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "svg", "aside"]):
        tag.decompose()

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
    """Extract clean readable text from HTML markup, discarding scripts/styles/noise."""
    if not html_str or not html_str.strip():
        return ""
    try:
        soup = BeautifulSoup(html_str, "html.parser")
        _clean_html_fragment(soup)
        return soup.get_text(separator="\n")
    except Exception:
        return ""


def _extract_best_content(item: dict[str, Any]) -> str:
    """Select the highest quality content representation from crawler dataset item.

    Prefers clean Markdown if it contains sufficient content (>= MIN_PAGE_CONTENT_CHARS).
    If Markdown is empty or sparse, falls back to text, content, or cleaned HTML.
    Never concatenates representations to prevent duplication.
    """
    markdown = (item.get("markdown") or "").strip()
    text = (item.get("text") or "").strip()
    content = (item.get("content") or "").strip()
    html = item.get("html") or ""

    # 1. Preferred representation: markdown with sufficient length
    if len(markdown) >= MIN_PAGE_CONTENT_CHARS:
        return markdown

    # 2. Candidate from direct text or content fields
    candidate = markdown or text or content

    # 3. If candidate is too short (< MIN_PAGE_CONTENT_CHARS), check if HTML provides fuller content
    if html:
        html_text = _extract_text_from_html(html).strip()
        if len(html_text) >= MIN_PAGE_CONTENT_CHARS and len(html_text) > len(candidate):
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
    page_timeout = min(max(timeout_seconds // len(urls), 20), 30) if urls else 30
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
        "pageLoadTimeoutSecs": page_timeout,
        "navigationTimeoutSecs": page_timeout,
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

    try:
        start_urls = [item.get("url") for item in actor_input.get("startUrls", [])]
        logger.info(
            "Starting Apify Actor '%s' for %d URLs (maxCrawlDepth=0).",
            WEBSITE_CONTENT_CRAWLER_ACTOR_ID,
            len(start_urls),
        )

        timeout_delta = datetime.timedelta(seconds=timeout_seconds)
        # Pass logger=None to prevent background log-streaming threads from raising
        # RemoteProtocolError or streaming transport exceptions.
        run = client.actor(WEBSITE_CONTENT_CRAWLER_ACTOR_ID).call(
            run_input=actor_input,
            run_timeout=timeout_delta,
            wait_duration=timeout_delta,
            logger=None,
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

        logger.info("Actor run '%s' completed with status '%s'.", run_id, actor_status)

        # Attempt to retrieve dataset items whenever a dataset ID is present,
        # including when run_status is TIMED-OUT or ABORTED, so partial results are preserved.
        dataset_id = (
            getattr(run, "default_dataset_id", None)
            or (run.get("defaultDatasetId") or run.get("default_dataset_id") if isinstance(run, dict) else None)
        )

        items: list[dict[str, Any]] = []
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
                logger.warning(
                    "Actor run '%s': failed to retrieve items from dataset '%s': %s",
                    run_id,
                    dataset_id,
                    ds_exc,
                )
                items = []
        else:
            logger.warning("Actor run '%s' (status '%s') has no default dataset ID.", run_id, actor_status)

        if actor_status == "SUCCEEDED":
            if not dataset_id:
                return [], run_id, "Actor run succeeded but returned no default dataset ID.", actor_status
            return items, run_id, None, actor_status

        # Actor ended with non-SUCCEEDED status (e.g. TIMED-OUT, ABORTED, FAILED)
        diag_msg = (
            f"Actor run ended with status '{actor_status}'"
            + (f": {status_message}" if status_message else ".")
        )
        if not dataset_id:
            diag_msg += " (No default dataset ID was returned by actor metadata)."
        elif not items:
            diag_msg += " (No dataset items were recovered from the run)."

        return items, run_id, diag_msg, actor_status

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
    actor_final_status: str | None = None

    for attempt in range(1, MAX_TRANSIENT_RETRIES + 1):
        logger.info(
            "Executing website content crawler (attempt %d/%d) for %d URLs...",
            attempt,
            MAX_TRANSIENT_RETRIES,
            len(urls_to_scrape),
        )
        sync_res = await asyncio.to_thread(
            _run_actor_sync,
            raw_token,
            actor_input,
            timeout_seconds,
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
        # Re-crawling all pages would waste time/cost and likely timeout again.
        if items:
            logger.info(
                "Recovered %d dataset items from run with status '%s'; avoiding full retry.",
                len(items),
                actor_final_status,
            )
            break

        # Non-retryable statuses (Auth errors, Not Found, Actor Timed-out with 0 items, Aborted)
        if last_error:
            lower_err = last_error.lower()
            if (
                "401" in last_error
                or "403" in last_error
                or "404" in last_error
                or "timed-out" in lower_err
                or "timeout" in lower_err
                or (actor_final_status and actor_final_status in ("TIMED-OUT", "ABORTED"))
            ):
                logger.warning(
                    "Non-retryable actor status ('%s') or error ('%s'); aborting retries.",
                    actor_final_status,
                    last_error,
                )
                break

        if attempt < MAX_TRANSIENT_RETRIES:
            logger.warning("Transient crawler error on attempt %d: %s. Retrying...", attempt, last_error)
            await asyncio.sleep(2.0 * attempt)

    # If actor execution yielded zero items (total failure, timeout with 0 items, or auth error)
    if not items:
        err_msg = last_error or "Actor execution failed to return any dataset items."
        failed_list = [
            FailedPageScrape(url=orig_u, error_message=f"Actor execution failed: {err_msg}", page_type=pt)
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
            actor_status=actor_final_status,
            execution_time_seconds=round(time.monotonic() - start_time, 2),
            error_message=err_msg,
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

        # Extract content using best available representation (Fix 5)
        raw_content = _extract_best_content(item)

        cleaned_text, is_blocked, is_truncated = _clean_page_content(raw_content)

        if is_blocked:
            page_failures.append(
                FailedPageScrape(
                    url=orig_url,
                    error_message="Page access denied or bot challenge detected.",
                    status_code=status_code,
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

        # Build ScrapedPage schema instance with honest HTTP status (Fix 6)
        scraped_page = ScrapedPage(
            url=orig_url,
            title=extracted_title,
            content=cleaned_text,
            page_type=p_type,
            status_code=status_code,
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
            if actor_final_status == "TIMED-OUT":
                miss_err = "Page not scraped: crawler run timed out before completion."
            elif actor_final_status and actor_final_status != "SUCCEEDED":
                miss_err = f"Page not scraped: crawler run ended with status '{actor_final_status}'."
            else:
                miss_err = "Page was not returned in the crawler dataset output."

            page_failures.append(
                FailedPageScrape(
                    url=orig_u,
                    error_message=miss_err,
                    page_type=p_type,
                )
            )

    # 8. Determine overall crawl status
    total_scraped = len(scraped_pages)
    total_failed = len(page_failures)
    has_usable_content = total_scraped > 0

    if total_scraped == total_requested and actor_final_status == "SUCCEEDED":
        overall_status: Literal["success", "partial", "empty", "failed"] = "success"
    elif total_scraped > 0:
        overall_status = "partial"
    else:
        # Items were returned, but none produced usable content (all blocked or empty)
        overall_status = "empty" if any(f.error_message and "too short" in f.error_message for f in page_failures) else "failed"

    exec_time = round(time.monotonic() - start_time, 2)
    logger.info(
        "Website content crawler finished for %s: status=%s (actor_status=%s), scraped=%d/%d, failed=%d (time=%.2fs).",
        resolved_website_url,
        overall_status,
        actor_final_status,
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
        actor_status=actor_final_status,
        execution_time_seconds=exec_time,
        error_message=last_error,
    )


# Backward-compatible alias
crawl_website_content = crawl_selected_pages
