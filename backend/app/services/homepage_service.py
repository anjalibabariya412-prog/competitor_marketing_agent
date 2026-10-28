"""Homepage fetching service using the Apify Playwright Scraper Actor.

Fetches a single rendered homepage for a given business URL via the
``apify/playwright-scraper`` Actor (Actor ID: apify/playwright-scraper).

Responsibilities:
- Validate and normalise the input URL.
- Invoke the Apify Actor with a minimal, bounded input configuration.
- Retrieve rendered HTML, final URL, and HTTP status from the Actor dataset.
- Detect CAPTCHA / access-denied responses without attempting bypass.
- Apply a small retry policy (up to 2 attempts) for transient Actor failures.
- Return a clear structured result regardless of outcome.

This module is deliberately narrow in scope. It does not perform link
extraction, sitemap crawling, OpenAI calls, or any other pipeline stage.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
import urllib.parse
from typing import Literal

from apify_client import ApifyClient
from apify_client.errors import ApifyApiError
from pydantic import BaseModel, Field

from backend.app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Verified Actor identifier – confirmed via https://api.apify.com/v2/acts/apify~playwright-scraper
PLAYWRIGHT_ACTOR_ID = "apify/playwright-scraper"

# Maximum seconds to allow the Apify Actor run to complete.
DEFAULT_ACTOR_TIMEOUT_SECONDS = 120

# Number of retries for transient Actor failures (network hiccup, Actor startup error).
# Permanent errors (auth failure, bad URL) are NOT retried.
MAX_TRANSIENT_RETRIES = 2

# Substrings whose presence in HTML strongly indicates a CAPTCHA or access-denial.
# Detection is intentionally lightweight; no bypass is attempted.
_BLOCK_SIGNALS: tuple[str, ...] = (
    "captcha",
    "g-recaptcha",
    "hcaptcha",
    "cf-challenge",          # Cloudflare challenge page
    "access denied",
    "403 forbidden",
    "451 unavailable",
    "you have been blocked",
    "bot detected",
    "please verify you are a human",
    "ddos-guard",
    "ray id",                # Cloudflare Ray ID in error pages
)

# ---------------------------------------------------------------------------
# pageFunction – JavaScript executed inside the Playwright browser context.
#
# The function is a string because it runs inside the Apify Actor's Node.js
# environment, not in Python.  It:
#   1. Retrieves the full rendered HTML via page.content().
#   2. Records page.url() as the final URL after any redirects.
#   3. Records the HTTP response status via the context's `response` object.
#   4. Returns all three values so they land in the Actor's dataset.
#
# Reference: https://apify.com/apify/playwright-scraper – pageFunction context
# ---------------------------------------------------------------------------

_PAGE_FUNCTION = """\
async function pageFunction(context) {
    const { page, request, response, log } = context;

    // Capture the final URL after all redirects.
    const finalUrl = page.url();

    // Capture the full rendered HTML.
    const html = await page.content();

    // HTTP status code is safely extracted as a number or null.
    // In Apify playwright-scraper, context.response is { status: number, headers: object }.
    let httpStatusCode = null;
    if (response) {
        if (typeof response.status === 'number') {
            httpStatusCode = response.status;
        } else if (typeof response.status === 'function') {
            try { httpStatusCode = response.status(); } catch (e) {}
        }
    }

    if (log && typeof log.info === 'function') {
        log.info('Fetched ' + finalUrl + ' (HTTP ' + httpStatusCode + ', ' + html.length + ' bytes)');
    }

    return {
        requestedUrl: request ? request.url : null,
        finalUrl: finalUrl,
        html: html,
        httpStatusCode: httpStatusCode,
    };
}
"""

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

HomepageStatus = Literal["success", "blocked", "empty_response", "failed"]


class HomepageFetchResult(BaseModel):
    """Structured result returned by :func:`fetch_homepage`."""

    requested_url: str = Field(description="The URL that was requested.")
    final_url: str | None = Field(
        default=None,
        description="Final URL after redirects, as reported by the browser. "
                    "None when the Actor did not return one.",
    )
    html: str | None = Field(
        default=None,
        description="Full rendered HTML of the homepage. None on failure.",
    )
    status: HomepageStatus = Field(
        description=(
            "Outcome of the fetch operation: "
            "'success' – HTML retrieved; "
            "'blocked' – CAPTCHA or access-denial detected; "
            "'empty_response' – Actor succeeded but returned no usable HTML; "
            "'failed' – Actor run failed or a configuration/auth error occurred."
        ),
    )
    http_status_code: int | None = Field(
        default=None,
        description="HTTP status code reported by the browser during navigation. "
                    "None when not reported by the Actor.",
    )
    error_message: str | None = Field(
        default=None,
        description="Human-readable error description. None on success.",
    )
    execution_time_seconds: float = Field(
        default=0.0,
        description="Total wall-clock time for the fetch operation.",
    )
    actor_run_id: str | None = Field(
        default=None,
        description="Apify Actor run ID for debugging. None when the run did not start.",
    )


# ---------------------------------------------------------------------------
# URL normalisation
# ---------------------------------------------------------------------------

def _normalize_url(url: str) -> str:
    """Normalize a website URL to a canonical ``https://`` form.

    - Strips whitespace.
    - Prepends ``https://`` when no scheme is present.
    - Removes trailing slashes from the path root.

    Raises:
        ValueError: When the URL is empty or has a non-HTTP(S) scheme.
    """
    cleaned = url.strip()
    if not cleaned:
        raise ValueError("URL cannot be empty.")

    if cleaned.startswith("//"):
        cleaned = f"https:{cleaned}"
    elif "://" not in cleaned:
        cleaned = f"https://{cleaned}"

    parsed = urllib.parse.urlsplit(cleaned)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(
            f"URL must use an http or https scheme, got '{scheme}'."
        )
    if not parsed.netloc:
        raise ValueError("URL must contain a valid domain name.")

    # Rebuild without fragment, preserving path, query and port
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/") or "/", parsed.query, "")
    )


# ---------------------------------------------------------------------------
# Blocking detection
# ---------------------------------------------------------------------------

def _is_blocked_html(html: str) -> bool:
    """Return True if the HTML content signals a CAPTCHA or access denial.

    Detection is based on well-known plain-text markers.  No bypass is
    attempted; this function is for classification only.
    """
    lower = html.lower()
    return any(signal in lower for signal in _BLOCK_SIGNALS)


# ---------------------------------------------------------------------------
# Actor input builder
# ---------------------------------------------------------------------------

def _build_actor_input(url: str) -> dict:
    """Construct the minimal Apify Playwright Scraper input for a single-page fetch.

    Configuration choices:
    - ``maxPagesPerCrawl=1`` / ``maxResultsPerCrawl=1``: fetch exactly one page.
    - ``globs=[]`` / ``pseudoUrls=[]`` / ``linkSelector=""``: no link following.
    - ``waitUntil="networkidle"``: wait for JS-rendered content.
    - ``launcher="chromium"``: default headless browser.
    - ``proxyConfiguration={"useApifyProxy": True}``: required by the Actor.
    - ``maxRequestRetries=0``: we handle retries at the Python layer; Actor
      internal retries would multiply cost unnecessarily.
    - ``downloadMedia=False`` / ``downloadCss=False``: reduce bandwidth and
      Actor compute time for a raw homepage fetch.
    """
    return {
        "startUrls": [{"url": url}],
        "globs": [],
        "pseudoUrls": [],
        "excludes": [],
        "linkSelector": "",
        "keepUrlFragments": False,
        "respectRobotsTxtFile": False,
        "pageFunction": _PAGE_FUNCTION,
        "proxyConfiguration": {"useApifyProxy": True},
        "proxyRotation": "RECOMMENDED",
        "initialCookies": [],
        "launcher": "chromium",
        "useChrome": False,
        "headless": True,
        "ignoreSslErrors": False,
        "ignoreCorsAndCsp": False,
        "downloadMedia": False,
        "downloadCss": False,
        "maxRequestRetries": 0,
        "maxPagesPerCrawl": 1,
        "maxResultsPerCrawl": 1,
        "maxCrawlingDepth": 0,
        "maxConcurrency": 1,
        "pageLoadTimeoutSecs": 60,
        "pageFunctionTimeoutSecs": 60,
        "waitUntil": "networkidle",
        "closeCookieModals": False,
        "maxScrollHeightPixels": 0,
        "debugLog": False,
        "browserLog": False,
        "customData": {},
    }


# ---------------------------------------------------------------------------
# Synchronous Actor runner (executed in a worker thread)
# ---------------------------------------------------------------------------

def _run_actor_sync(
    token: str,
    actor_input: dict,
    timeout_seconds: int,
) -> tuple[list[dict], str | None, str | None]:
    """Execute the Apify Playwright Scraper Actor synchronously.

    Intended to be called via ``asyncio.to_thread`` so it does not
    block the event loop.

    Returns:
        (dataset_items, actor_run_id, error_message)
        - ``dataset_items``: list of dicts returned by the Actor dataset.
        - ``actor_run_id``: Apify run ID string, or None if the run did not start.
        - ``error_message``: Human-readable failure description, or None on success.
          The Apify API token is never included in error messages.
    """
    client = ApifyClient(token=token)
    run_id: str | None = None

    try:
        logger.info(
            "Starting Apify Actor '%s' for URL: %s",
            PLAYWRIGHT_ACTOR_ID,
            actor_input.get("startUrls", [{}])[0].get("url", "<unknown>"),
        )

        # .call() starts the Actor and waits for it to finish.
        # run_timeout configures the Actor run timeout on the Apify platform.
        # wait_duration sets how long the client waits synchronously.
        timeout_delta = datetime.timedelta(seconds=timeout_seconds)
        run = client.actor(PLAYWRIGHT_ACTOR_ID).call(
            run_input=actor_input,
            run_timeout=timeout_delta,
            wait_duration=timeout_delta,
        )

        if run is None:
            return [], None, "Actor.call() returned None; run did not start."

        # In apify-client >= 2.0 / 3.0, call() returns a Pydantic Run model.
        # Support both model attributes and dict access defensively.
        run_id = getattr(run, "id", None) or (run.get("id") if isinstance(run, dict) else None)
        run_status = getattr(run, "status", None) or (run.get("status") if isinstance(run, dict) else None)

        logger.info("Actor run '%s' finished with status: %s", run_id, run_status)

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
            return [], run_id, "Actor run succeeded but returned no dataset ID."

        items = list(client.dataset(dataset_id).iterate_items())
        logger.info(
            "Actor run '%s': retrieved %d item(s) from dataset '%s'.",
            run_id,
            len(items),
            dataset_id,
        )
        return items, run_id, None

    except ApifyApiError as exc:
        # ApifyApiError carries a status_code; 401/403 indicate auth failure.
        status_code = getattr(exc, "status_code", None)
        if status_code in (401, 403):
            # Do not include the token in the message.
            return [], run_id, "Apify authentication failed (HTTP 401/403). Check APIFY_API_TOKEN."
        if status_code == 400:
            return [], run_id, f"Apify input validation error (HTTP 400): {getattr(exc, 'message', None) or exc}"
        if status_code == 404:
            return [], run_id, f"Apify Actor not found (HTTP 404): {getattr(exc, 'message', None) or exc}"
        msg = getattr(exc, "message", None) or str(exc)
        return [], run_id, f"Apify API error: {msg}"

    except Exception as exc:
        return [], run_id, f"Unexpected error during Actor run: {exc}"


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def _extract_dataset_payload(
    items: list[dict],
) -> tuple[dict | None, str | None]:
    """Inspect dataset items to extract a valid page item or a descriptive error.

    Dataset items may contain:
    - Success items from pageFunction: { requestedUrl, finalUrl, html, httpStatusCode, ... }
    - Error items from failedRequestHandler: { "#error": true, "#debug": { ... } }

    Returns:
        (valid_item, error_message)
        - If a valid non-error item with HTML is present: (item, None)
        - If items contain #error flags or navigation failure: (None, error_msg)
        - If non-error items exist but HTML is empty: (item, None)
        - If items is empty: (None, "Dataset is empty.")
    """
    if not items:
        return None, "Dataset is empty."

    # 1. Prefer a successful non-error item that returned HTML content
    for item in items:
        if not item.get("#error") and item.get("html"):
            return item, None

    # 2. Check if any items are error-flagged items from failedRequestHandler
    for item in items:
        if item.get("#error"):
            debug = item.get("#debug") or {}
            error_messages = debug.get("errorMessages") or []
            if error_messages and isinstance(error_messages, list):
                msg = error_messages[0].split("\n")[0]
            else:
                msg = debug.get("errorMessage") or item.get("errorMessage") or "Page navigation failed inside Actor."
            return None, f"Actor crawl error: {msg}"

    # 3. If there are items without #error but with empty HTML, return the first one
    return items[0], None


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------

async def fetch_homepage(
    website_url: str,
    *,
    timeout_seconds: int = DEFAULT_ACTOR_TIMEOUT_SECONDS,
) -> HomepageFetchResult:
    """Fetch the rendered homepage for the given business website URL.

    This is the sole public entry point for this service.  Callers pass a raw
    URL string; this function normalises it, invokes the Apify Actor, parses
    the result, and returns a :class:`HomepageFetchResult`.

    The Apify API token is read from ``settings.APIFY_API_TOKEN`` and is never
    logged or included in error messages.

    A small bounded retry policy (up to :data:`MAX_TRANSIENT_RETRIES` total
    attempts) covers transient Actor failures. Permanent errors such as auth
    failures or URL validation errors are not retried.

    Args:
        website_url: The raw business website URL to fetch.
        timeout_seconds: Maximum seconds to allow the Actor run to complete.

    Returns:
        A populated :class:`HomepageFetchResult` instance.
    """
    start_time = time.monotonic()

    # --- 1. Validate and normalise the URL ---
    try:
        normalized_url = _normalize_url(website_url)
    except ValueError as exc:
        return HomepageFetchResult(
            requested_url=website_url,
            status="failed",
            error_message=f"Invalid URL: {exc}",
            execution_time_seconds=round(time.monotonic() - start_time, 2),
        )

    # --- 2. Validate Apify token ---
    if not settings.APIFY_API_TOKEN:
        return HomepageFetchResult(
            requested_url=normalized_url,
            status="failed",
            error_message="APIFY_API_TOKEN is not configured in settings.",
            execution_time_seconds=round(time.monotonic() - start_time, 2),
        )

    raw_token: str = settings.APIFY_API_TOKEN.get_secret_value().strip()
    if not raw_token or raw_token in ("your_apify_api_token_here", ""):
        return HomepageFetchResult(
            requested_url=normalized_url,
            status="failed",
            error_message="APIFY_API_TOKEN is set to a placeholder value.",
            execution_time_seconds=round(time.monotonic() - start_time, 2),
        )

    actor_input = _build_actor_input(normalized_url)

    # --- 3. Run the Actor with bounded retries ---
    items: list[dict] = []
    run_id: str | None = None
    error_message: str | None = None

    for attempt in range(1, MAX_TRANSIENT_RETRIES + 1):
        logger.info(
            "Homepage fetch attempt %d/%d for URL: %s",
            attempt,
            MAX_TRANSIENT_RETRIES,
            normalized_url,
        )

        items, run_id, error_message = await asyncio.to_thread(
            _run_actor_sync,
            raw_token,
            actor_input,
            timeout_seconds,
        )

        if not error_message:
            # Success — break out of retry loop
            break

        # Do not retry permanent failures
        err_lower = (error_message or "").lower()
        is_permanent = (
            "authentication failed" in err_lower
            or "401" in (error_message or "")
            or "403" in (error_message or "")
            or "validation error" in err_lower
            or "400" in (error_message or "")
            or "not found" in err_lower
            or "404" in (error_message or "")
        )
        if is_permanent:
            logger.error(
                "Permanent Actor failure on attempt %d; not retrying. Run ID: %s",
                attempt,
                run_id,
            )
            break

        if attempt < MAX_TRANSIENT_RETRIES:
            backoff = 2 ** attempt  # 2s, 4s …
            logger.warning(
                "Transient Actor failure on attempt %d; retrying in %ds. Error: %s",
                attempt,
                backoff,
                error_message,
            )
            await asyncio.sleep(backoff)

    elapsed = round(time.monotonic() - start_time, 2)

    # --- 4. Handle complete Actor failure or empty dataset ---
    if error_message:
        logger.error(
            "Homepage fetch failed for %s after %d attempt(s). Run ID: %s. Error: %s",
            normalized_url,
            MAX_TRANSIENT_RETRIES,
            run_id,
            error_message,
        )
        return HomepageFetchResult(
            requested_url=normalized_url,
            status="failed",
            error_message=error_message,
            actor_run_id=run_id,
            execution_time_seconds=elapsed,
        )

    if not items:
        # Actor succeeded but returned an empty dataset
        logger.warning(
            "Actor run '%s' succeeded but returned no dataset items for %s.",
            run_id,
            normalized_url,
        )
        return HomepageFetchResult(
            requested_url=normalized_url,
            status="empty_response",
            error_message="Actor completed successfully but returned no page data.",
            actor_run_id=run_id,
            execution_time_seconds=elapsed,
        )

    # --- 5. Extract page data or crawl error from dataset items ---
    valid_item, dataset_error = _extract_dataset_payload(items)

    if dataset_error:
        logger.warning(
            "Actor dataset contains error items for %s. Run ID: %s. Error: %s",
            normalized_url,
            run_id,
            dataset_error,
        )
        return HomepageFetchResult(
            requested_url=normalized_url,
            status="failed",
            error_message=dataset_error,
            actor_run_id=run_id,
            execution_time_seconds=elapsed,
        )

    html: str | None = (valid_item.get("html") if valid_item else None) or None
    final_url: str | None = (valid_item.get("finalUrl") if valid_item else None) or None
    http_status_code: int | None = valid_item.get("httpStatusCode") if valid_item else None

    if not html:
        logger.warning(
            "Actor run '%s' returned an item but HTML is empty for %s.",
            run_id,
            normalized_url,
        )
        return HomepageFetchResult(
            requested_url=normalized_url,
            final_url=final_url,
            http_status_code=http_status_code,
            status="empty_response",
            error_message="Actor returned an empty HTML body.",
            actor_run_id=run_id,
            execution_time_seconds=elapsed,
        )

    # --- 6. Blocking / CAPTCHA detection ---
    if _is_blocked_html(html) or (http_status_code in (401, 403, 429)):
        logger.warning(
            "Homepage for %s appears to be blocked or CAPTCHA-gated "
            "(final URL: %s, HTTP %s). Run ID: %s.",
            normalized_url,
            final_url,
            http_status_code,
            run_id,
        )
        return HomepageFetchResult(
            requested_url=normalized_url,
            final_url=final_url,
            html=html,
            http_status_code=http_status_code,
            status="blocked",
            error_message=(
                "The response appears to be a CAPTCHA challenge or access-denial page. "
                "No bypass was attempted."
            ),
            actor_run_id=run_id,
            execution_time_seconds=elapsed,
        )

    # --- 7. Success ---
    logger.info(
        "Homepage fetch succeeded for %s -> %s (HTTP %s, %d bytes). "
        "Run ID: %s. Elapsed: %.2fs.",
        normalized_url,
        final_url,
        http_status_code,
        len(html),
        run_id,
        elapsed,
    )
    return HomepageFetchResult(
        requested_url=normalized_url,
        final_url=final_url,
        html=html,
        http_status_code=http_status_code,
        status="success",
        actor_run_id=run_id,
        execution_time_seconds=elapsed,
    )
