"""Content adequacy and quality validation service.

Evaluates whether scraped website text is substantive, meaningful, and usable
for competitive intelligence, rather than an empty JavaScript SPA skeleton,
a cookie-consent notice, or an active WAF/CAPTCHA block.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from typing import Any, Literal, Sequence
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

# ---------------------------------------------------------------------------
# Domain-Level HTTP Block Memory
# ---------------------------------------------------------------------------

_BLOCKED_HTTP_DOMAINS: set[str] = set()


def _normalize_domain_host(url: str) -> str:
    """Extract lowercase hostname stripped of www. prefix."""
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


def mark_domain_http_blocked(url: str, reason: str = "blocked") -> None:
    """Record that a domain has blocked direct HTTP requests (e.g. 403, 429, WAF)."""
    host = _normalize_domain_host(url)
    if host:
        _BLOCKED_HTTP_DOMAINS.add(host)
        logger.info("[HTTP_BLOCK_CACHE] Domain '%s' marked as HTTP-blocked (reason: %s).", host, reason)


def is_domain_http_blocked(url: str) -> bool:
    """Check if direct HTTP requests for this domain should be skipped due to prior block."""
    host = _normalize_domain_host(url)
    return bool(host and host in _BLOCKED_HTTP_DOMAINS)


def clear_domain_http_blocks() -> None:
    """Clear in-memory HTTP-blocked domains (used in tests or fresh runs)."""
    _BLOCKED_HTTP_DOMAINS.clear()


# ---------------------------------------------------------------------------
# Content Adequacy & Validation
# ---------------------------------------------------------------------------

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


def compute_text_overlap_ratio(text: str, existing_texts: Sequence[str]) -> float:
    """Compute the fraction of word 3-grams in `text` that appear in `existing_texts`.

    Returns a float between 0.0 and 1.0 representing cross-page duplicate boilerplate ratio.
    """
    if not text or not existing_texts:
        return 0.0
    words = [w.lower() for w in re.findall(r"\b\w+\b", text)]
    if len(words) < 3:
        cand_words = set(words)
        if not cand_words:
            return 0.0
        existing_words = {w.lower() for t in existing_texts for w in re.findall(r"\b\w+\b", t)}
        return len(cand_words.intersection(existing_words)) / len(cand_words)

    shingles_candidate = {tuple(words[i : i + 3]) for i in range(len(words) - 2)}
    if not shingles_candidate:
        return 0.0

    all_existing_shingles: set[tuple[str, ...]] = set()
    for t in existing_texts:
        e_words = [w.lower() for w in re.findall(r"\b\w+\b", t)]
        if len(e_words) >= 3:
            all_existing_shingles.update(tuple(e_words[i : i + 3]) for i in range(len(e_words) - 2))

    shared = shingles_candidate.intersection(all_existing_shingles)
    return len(shared) / len(shingles_candidate)


def evaluate_page_content_quality(
    content: str,
    page_type: PageType | str | None = None,
    existing_texts: Sequence[str] | None = None,
    url: str = "",
) -> tuple[Literal["primary", "weak", "inadequate"], str]:
    """Evaluate content substance, brevity, and cross-page duplication.

    Returns:
        (category, reason) where category is "primary", "weak", or "inadequate".
    """
    if not content or not content.strip():
        return "inadequate", "empty_content"

    text = content.strip()
    if is_blocked_content(text):
        return "inadequate", "blocked_content"

    char_count = len(text)
    if char_count < 100:
        return "inadequate", f"insufficient_readable_content ({char_count} chars < 100)"

    # Check for concise pricing or contact pages
    lower = text.lower()
    pt_str = page_type.value if hasattr(page_type, "value") else str(page_type or "").lower()
    is_pricing_or_contact = (
        pt_str in ("pricing", "contact", "policy")
        or any(k in url.lower() for k in ("pricing", "plan", "contact", "support"))
    )

    if is_pricing_or_contact and char_count >= 100:
        has_pricing_data = any(sym.lower() in lower for sym in ("$", "₹", "€", "£", "usd", "inr", "/mo", "/month", "/yr", "per year", "free tier", "pricing", "membership", "club", "gold", "bogo", "sale", "discount"))
        has_contact_data = "@" in text or any(term in lower for term in ("email", "tel:", "phone", "contact us", "support@"))
        if has_pricing_data or has_contact_data:
            return "primary", "concise_specialized_page"

    # Cross-page duplicate boilerplate check
    if existing_texts:
        overlap = compute_text_overlap_ratio(text, existing_texts)
        if overlap >= 0.70:
            return "weak", f"high_cross_page_overlap ({overlap:.0%})"

    if char_count < 300:
        return "weak", f"sparse_content ({char_count} chars < 300)"

    return "primary", "substantive_content"



# ---------------------------------------------------------------------------
# Boilerplate Filtering & Layered Fallback Extraction
# ---------------------------------------------------------------------------

_BOILERPLATE_REGEXES: list[re.Pattern[str]] = [
    re.compile(r"(?i)\b(we use cookies|cookie policy|cookie preferences|cookie settings|manage cookies|accept (?:all )?cookies|reject (?:all )?cookies|consent to our use of cookies|onetrust|powered by onetrust|cookiebot|strictly necessary cookies)\b[^\n\.]*[\n\.]?"),
    re.compile(r"(?i)\b(you need to enable javascript|please enable javascript|javascript is (?:required|disabled|needed))\b[^\n\.]*[\n\.]?"),
    re.compile(r"(?i)\b(attention required!\s*\|\s*cloudflare|cf-ray|ray id:\s*[a-f0-9]+|security check to continue|please verify you are a human)\b[^\n\.]*[\n\.]?"),
    re.compile(r"(?i)\b(all rights reserved|copyright\s*(?:©|\(c\)|&copy;)?\s*\b\d{4}\b)[^\n\.]*[\n\.]?"),
]


def clean_boilerplate(text: str) -> str:
    """Strip cookie consent notices, JS disabled notices, bot check messages, and copyright footers.

    Removes boilerplate lines and inline boilerplate fragments while preserving substantive page text.
    """
    if not text:
        return ""
    lines = text.splitlines()
    cleaned_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        lower = stripped.lower()
        if any(sig in lower for sig in SPA_SKELETON_SIGNALS):
            continue
        if any(sig in lower for sig in DEFINITIVE_BLOCK_SIGNALS):
            continue
        if _COOKIE_BOILERPLATE_REGEX.search(stripped) and len(stripped) < 250:
            continue
        if "all rights reserved" in lower and (("copyright" in lower) or ("©" in lower) or ("(c)" in lower) or re.search(r"\b20\d\d\b", lower)):
            continue
        res = stripped
        for reg in _BOILERPLATE_REGEXES:
            res = reg.sub("", res).strip()
        if res:
            cleaned_lines.append(res)
    return "\n".join(cleaned_lines).strip()


def _extract_from_json_ld_node(node: Any, lines: list[str]) -> None:
    """Recursively traverse JSON-LD entity structures and append concise factual lines."""
    if isinstance(node, list):
        for item in node:
            _extract_from_json_ld_node(item, lines)
        return
    if not isinstance(node, dict):
        return

    if "@graph" in node and isinstance(node["@graph"], list):
        for item in node["@graph"]:
            _extract_from_json_ld_node(item, lines)

    node_type = str(node.get("@type") or node.get("type") or "").strip()
    name = str(node.get("name") or "").strip()
    legal_name = str(node.get("legalName") or "").strip()
    desc = str(node.get("description") or "").strip()
    slogan = str(node.get("slogan") or "").strip()

    if any(t in node_type.lower() for t in ("organization", "store", "corporation", "localbusiness")):
        display_name = name or legal_name
        if name and legal_name and name.lower() != legal_name.lower():
            display_name = f"{name} ({legal_name})"
        entry = f"Organization: {display_name}" if display_name else "Organization"
        if desc:
            entry += f" - {desc}"
        if slogan:
            entry += f" (Slogan: {slogan})"
        lines.append(entry)
        knows_about = node.get("knowsAbout")
        if isinstance(knows_about, list):
            lines.append("Offerings / Specialties: " + ", ".join(str(k) for k in knows_about if k))
        elif isinstance(knows_about, str) and knows_about:
            lines.append(f"Offerings / Specialties: {knows_about}")

    elif "website" in node_type.lower():
        entry = f"Website: {name}" if name else "Website"
        if desc:
            entry += f" - {desc}"
        lines.append(entry)

    elif "product" in node_type.lower():
        entry = f"Product: {name}" if name else "Product"
        brand = node.get("brand")
        if isinstance(brand, dict):
            b_name = brand.get("name")
            if b_name:
                entry += f" (Brand: {b_name})"
        elif isinstance(brand, str) and brand:
            entry += f" (Brand: {brand})"
        if desc:
            entry += f" - {desc}"
        offers = node.get("offers")
        if isinstance(offers, dict):
            price = offers.get("price")
            curr = offers.get("priceCurrency") or ""
            if price:
                entry += f" [Price: {price} {curr}]"
        elif isinstance(offers, list) and offers:
            first_offer = offers[0]
            if isinstance(first_offer, dict):
                price = first_offer.get("price")
                curr = first_offer.get("priceCurrency") or ""
                if price:
                    entry += f" [Price: {price} {curr}]"
        lines.append(entry)

    elif "service" in node_type.lower():
        entry = f"Service: {name}" if name else "Service"
        if desc:
            entry += f" - {desc}"
        lines.append(entry)

    elif any(t in node_type.lower() for t in ("itemlist", "breadcrumblist")):
        items = node.get("itemListElement") or []
        item_names = []
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    it_name = it.get("name") or (it.get("item", {}).get("name") if isinstance(it.get("item"), dict) else None)
                    if it_name:
                        item_names.append(str(it_name).strip())
        if item_names:
            lines.append("Catalog Items: " + ", ".join(item_names))

    elif name or desc:
        entry = f"{node_type or 'Item'}: {name}" if name else (node_type or "Item")
        if desc:
            entry += f" - {desc}"
        lines.append(entry)


def extract_structured_fallback_text(html: str) -> str:
    """Extract structured fallback text from JSON-LD schema, meta tags, headings, and CTAs.

    Used when primary HTML body text is too short (< MIN_FALLBACK_EXTRACT_CHARS) or empty (e.g. SPAs).
    """
    if not html or not html.strip():
        return ""
    try:
        soup = BeautifulSoup(html, "html.parser")

        # 1. Parse JSON-LD scripts
        json_ld_lines: list[str] = []
        for script in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
            try:
                raw_json = script.string or script.get_text()
                if not raw_json:
                    continue
                data = json.loads(raw_json.strip())
                _extract_from_json_ld_node(data, json_ld_lines)
            except Exception:
                continue

        # 2. Extract Title & Meta Description
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        if not title:
            og_title = soup.find("meta", attrs={"property": re.compile(r"^(og:title|twitter:title)$", re.I)})
            if og_title and og_title.get("content"):
                title = og_title["content"].strip()

        meta_desc = ""
        for tag in soup.find_all("meta"):
            name = (tag.get("name") or tag.get("property") or "").lower()
            content = (tag.get("content") or "").strip()
            if not content:
                continue
            if name in ("description", "og:description", "twitter:description") and not meta_desc:
                meta_desc = content

        # 3. Headings
        headings: list[str] = []
        for h in soup.find_all(["h1", "h2", "h3"]):
            htext = h.get_text(separator=" ", strip=True)
            if htext and len(htext) > 2 and htext not in headings:
                clean_h = clean_boilerplate(htext)
                if clean_h:
                    headings.append(f"- {clean_h}")

        # 4. Buttons / CTAs
        buttons: list[str] = []
        for btn in soup.find_all(["button", "a"]):
            is_btn = (
                btn.name == "button"
                or "btn" in (btn.get("class") or [])
                or (btn.get("role") or "").lower() == "button"
            )
            if is_btn:
                btext = btn.get_text(separator=" ", strip=True)
                if btext and 3 < len(btext) < 60 and btext not in buttons:
                    if not any(noise in btext.lower() for noise in ("menu", "close", "toggle", "open", "accept", "reject")):
                        clean_b = clean_boilerplate(btext)
                        if clean_b:
                            buttons.append(clean_b)

        # 5. Image Alt attributes
        alts: list[str] = []
        for img in soup.find_all("img"):
            alt = (img.get("alt") or "").strip()
            if alt and len(alt) >= 6 and alt not in alts:
                if not any(noise in alt.lower() for noise in ("logo", "icon", "arrow", "spacer", "placeholder", "loading")):
                    clean_a = clean_boilerplate(alt)
                    if clean_a:
                        alts.append(clean_a)

        sections: list[str] = []
        if title:
            sections.append(f"[Page Title]\n{title}")
        if meta_desc:
            sections.append(f"[Meta Description]\n{meta_desc}")
        if headings:
            sections.append("[Key Headings]\n" + "\n".join(headings[:15]))
        if json_ld_lines:
            deduped_json: list[str] = []
            seen_j: set[str] = set()
            for jl in json_ld_lines:
                if jl not in seen_j:
                    seen_j.add(jl)
                    deduped_json.append(jl)
            sections.append("[Structured Data (JSON-LD)]\n" + "\n".join(deduped_json))
        if buttons:
            sections.append("[Key Actions & Offerings]\n" + "\n".join(f"- {b}" for b in buttons[:10]))
        if alts:
            sections.append("[Featured Visuals & Products]\n" + "\n".join(f"- {a}" for a in alts[:10]))

        return "\n\n".join(sections)
    except Exception:
        return ""


def extract_text_from_html(html: str) -> str:
    """Extract readable text from HTML by removing scripts, styles, and markup.

    Preserves substantive page text, cleans boilerplate, and if content is sparse
    (< MIN_FALLBACK_EXTRACT_CHARS), augments with structured fallback.
    """
    if not html or not html.strip():
        return ""
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
            tag.decompose()
        raw_text = soup.get_text(separator=" ", strip=True)
        cleaned = clean_boilerplate(raw_text)

        min_fallback = 500
        try:
            from backend.app.core.config import settings
            min_fallback = getattr(settings, "MIN_FALLBACK_EXTRACT_CHARS", 500)
        except Exception:
            pass

        if len(cleaned) < min_fallback:
            fallback = extract_structured_fallback_text(html)
            if fallback:
                if cleaned:
                    return f"{cleaned}\n\n{fallback}".strip()
                return fallback.strip()

        return cleaned
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
