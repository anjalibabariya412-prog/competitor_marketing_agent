"""Content adequacy and quality validation service.

Evaluates whether scraped website text is substantive, meaningful, and usable
for competitive intelligence, rather than an empty JavaScript SPA skeleton,
a cookie-consent notice, or an active WAF/CAPTCHA block.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any
from bs4 import BeautifulSoup

from backend.app.schemas.enums import PageType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants & Quality Thresholds
# ---------------------------------------------------------------------------

DEFAULT_MIN_PAGE_CHARS: int = 50
DEFAULT_MIN_HOMEPAGE_CHARS: int = 50

# Active challenge signatures that signal bot detection or WAF blocking
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

AMBIGUOUS_BLOCK_SIGNALS: tuple[str, ...] = (
    "captcha",
    "g-recaptcha",
    "hcaptcha",
    "access denied",
    "403 forbidden",
    "451 unavailable",
    "bot detected",
    "ddos-guard",
    "ray id",
)

# SPA skeleton markers that indicate content was rendered only on the client via JS
SPA_SKELETON_SIGNALS: tuple[str, ...] = (
    "you need to enable javascript to run this app",
    "please enable javascript to view",
    "please enable javascript",
    "enable javascript to run this app",
    "javascript is required",
    "javascript is disabled",
    "enable js to view",
    "loading your experience",
)

_COOKIE_BOILERPLATE_REGEX = re.compile(
    r"(?i)\b(we use cookies|cookie policy|cookie preferences|cookie settings|manage cookies|manage preferences|"
    r"accept (?:all|cookies|necessary)|reject (?:all|cookies)|consent to our use of cookies|"
    r"terms of service|privacy policy|privacy notice|by clicking|by continuing|to improve your experience|"
    r"onetrust|powered by onetrust|gdpr|strictly necessary cookies)\b"
)


def is_blocked_content(text: str) -> bool:
    """Return True if text indicates an active CAPTCHA, WAF, or access block."""
    if not text:
        return False
    lower = text.lower()

    if any(sig in lower for sig in DEFINITIVE_BLOCK_SIGNALS):
        return True

    # Ambiguous signals only count if page is short (< 4000 chars)
    if len(text.strip()) < 4000:
        return any(sig in lower for sig in AMBIGUOUS_BLOCK_SIGNALS)

    return False


def is_content_adequate(
    content: str | None,
    *,
    url: str = "",
    page_type: PageType | None = None,
    min_chars: int | None = None,
) -> tuple[bool, str]:
    """Validate whether extracted page text is substantive and meaningful.

    Checks:
    - Text is not empty or whitespace.
    - Text does not represent an active CAPTCHA or WAF block.
    - Text does not consist solely of a JavaScript SPA skeleton message.
    - Text is not solely cookie-banner consent boilerplate.
    - Content meets conservative length thresholds based on page category.

    Args:
        content: Extracted plain text / markdown from HTML.
        url: URL of the page being evaluated.
        page_type: Optional PageType enum classification.
        min_chars: Optional explicit minimum character threshold.

    Returns:
        (is_adequate: bool, reason: str)
    """
    if not content or not content.strip():
        return False, "empty_content"

    text = content.strip()
    char_count = len(text)

    # 1. Check for active CAPTCHA or WAF block
    if is_blocked_content(text):
        return False, "blocked_content"

    lower = text.lower()

    # 2. Check for SPA skeleton requiring client-side JS rendering
    if char_count < 400 and any(sig in lower for sig in SPA_SKELETON_SIGNALS):
        return False, "spa_skeleton_requires_javascript"

    # 3. Check for cookie-consent only pages (e.g. Lenskart 178 chars)
    if char_count < 300 and _COOKIE_BOILERPLATE_REGEX.search(text):
        stripped_cookie = _COOKIE_BOILERPLATE_REGEX.sub("", text)
        alphanumeric_count = sum(1 for c in stripped_cookie if c.isalnum())
        if alphanumeric_count < 35:
            return False, "cookie_notice_only"

    # 4. Threshold check
    is_home = page_type == PageType.HOME or url.rstrip("/").count("/") <= 2
    required_min = min_chars if min_chars is not None else (
        DEFAULT_MIN_HOMEPAGE_CHARS if is_home else DEFAULT_MIN_PAGE_CHARS
    )

    if char_count < required_min:
        # Allow short contact or policy pages if they contain substantive contact/policy data
        if page_type in (PageType.POLICY, PageType.ABOUT, PageType.CONTACT) or any(
            term in url.lower() for term in ("contact", "policy", "support", "privacy", "terms")
        ):
            if char_count >= DEFAULT_MIN_PAGE_CHARS and (
                "@" in text or any(term in lower for term in ("email", "phone", "tel:", "address", "policy", "rights"))
            ):
                return True, "content_adequate"

        return False, f"too_short ({char_count} chars < {required_min} required)"

    return True, "content_adequate"


def extract_text_from_html(html: str) -> str:
    """Extract readable text from HTML by removing scripts, styles, and markup.

    Preserves substantive page text while stripping noise tags.
    """
    if not html or not html.strip():
        return ""
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Diagnostics & Sanitization Helpers
# ---------------------------------------------------------------------------

_SENSITIVE_PARAM_NAMES = frozenset({
    "token", "api_key", "apikey", "key", "secret", "password", "pass", "pwd",
    "auth", "authorization", "access_token", "refresh_token", "session",
    "session_id", "sid", "signature", "sig", "bearer", "credential", "credentials",
})

_SENSITIVE_PARAM_SUFFIXES = ("_token", "_key", "_secret", "_id", "_auth", "_sig")


def sanitize_url_for_logging(url: str) -> str:
    """Sanitize URL query parameters by redacting sensitive keys (tokens, keys, secrets)."""
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlsplit(url)
        if not parsed.query:
            return url

        query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        sanitized_params: list[tuple[str, str]] = []

        for k, v in query_params:
            k_lower = k.lower()
            if k_lower in _SENSITIVE_PARAM_NAMES or any(k_lower.endswith(sfx) for sfx in _SENSITIVE_PARAM_SUFFIXES):
                sanitized_params.append((k, "[REDACTED]"))
            else:
                sanitized_params.append((k, v))

        new_query = urllib.parse.urlencode(sanitized_params, doseq=True)
        return urllib.parse.urlunsplit((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            new_query,
            parsed.fragment,
        ))
    except Exception:
        if "?" in url:
            base = url.split("?", 1)[0]
            return f"{base}?[REDACTED]"
        return url


def sanitize_body_preview(text: str | None, max_chars: int = 250) -> str:
    """Sanitize and truncate response body preview for single-line log readability."""
    if not text:
        return ""
    try:
        collapsed = re.sub(r"\s+", " ", text).strip()
        collapsed = re.sub(r"bearer\s+[A-Za-z0-9_\-\.]+", "bearer [REDACTED]", collapsed, flags=re.IGNORECASE)
        collapsed = re.sub(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+", "[REDACTED_JWT]", collapsed)

        if len(collapsed) > max_chars:
            return collapsed[:max_chars].rstrip() + "..."
        return collapsed
    except Exception:
        return ""


def log_http_response_diagnostics(
    resp: Any,
    *,
    stage: str,
    duration_seconds: float | None = None,
    logger_instance: logging.Logger | None = None,
) -> None:
    """Log structured diagnostics for HTTP responses, specifically highlighting HTTP 429 and non-200.

    Extracts:
    - Status code
    - Sanitized URL
    - Case-insensitive headers: Retry-After, Server, CF-Ray, X-Request-ID, X-Correlation-ID
    - Sanitized, truncated body preview
    - Request duration and retrieval stage

    Never throws an exception or interrupts execution.
    """
    target_logger = logger_instance or logger
    try:
        status_code = getattr(resp, "status_code", 0)
        raw_url = str(getattr(resp, "url", ""))
        sanitized_url = sanitize_url_for_logging(raw_url)

        headers = getattr(resp, "headers", None)
        header_get = getattr(headers, "get", None) if headers is not None else None

        def _get_hdr(name: str) -> str | None:
            if header_get is not None:
                val = header_get(name) or header_get(name.lower()) or header_get(name.title())
                if val is not None:
                    return str(val).strip()
            elif isinstance(headers, dict):
                lower_map = {str(k).lower(): str(v) for k, v in headers.items()}
                return lower_map.get(name.lower())
            return None

        retry_after = _get_hdr("retry-after")
        server = _get_hdr("server")
        cf_ray = _get_hdr("cf-ray")
        x_request_id = _get_hdr("x-request-id")
        x_correlation_id = _get_hdr("x-correlation-id")

        duration_str = f"{duration_seconds:.2f}s" if duration_seconds is not None else "N/A"
        raw_body = getattr(resp, "text", "")
        body_preview = sanitize_body_preview(raw_body, max_chars=250)

        if status_code == 429:
            target_logger.warning(
                "[HTTP_DIAGNOSTIC] HTTP 429 (Too Many Requests) | stage=%s | url=%s | status=429 | "
                "duration=%s | retry_after=%s | server=%s | cf_ray=%s | x_request_id=%s | "
                "x_correlation_id=%s | body_preview=%r",
                stage,
                sanitized_url,
                duration_str,
                retry_after if retry_after is not None else "None",
                server if server is not None else "None",
                cf_ray if cf_ray is not None else "None",
                x_request_id if x_request_id is not None else "None",
                x_correlation_id if x_correlation_id is not None else "None",
                body_preview,
            )
        else:
            target_logger.warning(
                "[HTTP_DIAGNOSTIC] HTTP %s | stage=%s | url=%s | status=%s | duration=%s | "
                "retry_after=%s | server=%s | cf_ray=%s | x_request_id=%s | "
                "x_correlation_id=%s | body_preview=%r",
                status_code,
                stage,
                sanitized_url,
                status_code,
                duration_str,
                retry_after if retry_after is not None else "None",
                server if server is not None else "None",
                cf_ray if cf_ray is not None else "None",
                x_request_id if x_request_id is not None else "None",
                x_correlation_id if x_correlation_id is not None else "None",
                body_preview,
            )
    except Exception as exc:
        target_logger.debug("Failed to extract or log HTTP response diagnostics: %s", exc)


