"""Homepage URL extraction and cleaning service.

Extracts, cleans, normalizes, deduplicates, and classifies candidate internal
website URLs from rendered homepage HTML (as returned by ``fetch_homepage``).

Responsibilities:
- Parse rendered HTML using BeautifulSoup.
- Prioritize header, navigation, and footer links.
- Fall back to main content links when fewer than 5 unique priority links are found.
- Resolve relative links against the homepage's final/requested URL.
- Retain only HTTP/HTTPS links belonging to the same business website.
- Filter out tracking parameters, static media assets, and utility/auth routes
  (login, cart, checkout) while preserving policy and location pages.
- Assign preliminary PageType classifications based on path and anchor text keywords.
- Return structured, deterministic results without fabricating URLs.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any, Literal

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field

from backend.app.schemas.enums import PageType
from .homepage_service import HomepageFetchResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants & Filtering Rules
# ---------------------------------------------------------------------------

# Tracking and analytical query parameter names to strip during normalization
_TRACKING_PARAMS: frozenset[str] = frozenset({
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "msclkid",
    "mc_eid",
    "_ga",
    "_gl",
    "ref",
    "ref_src",
    "source",
    "yclid",
})

# Static asset extensions that should never be crawled as candidate pages
_IGNORED_EXTENSIONS: frozenset[str] = frozenset({
    # Images & icons
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tiff",
    # Audio & video
    ".mp4", ".mp3", ".wav", ".mov", ".avi", ".webm", ".ogg", ".flv",
    # Documents & archives
    ".pdf", ".zip", ".tar", ".gz", ".rar", ".7z", ".doc", ".docx", ".xls", ".xlsx",
    # Code & assets
    ".css", ".js", ".json", ".xml", ".csv", ".tsv",
    # Fonts
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    # Executables
    ".exe", ".dmg", ".apk", ".bin",
})

# Route segments indicating non-content, transactional, or administrative endpoints
_EXCLUDED_PATH_SEGMENTS: frozenset[str] = frozenset({
    # Authentication & Accounts
    "login", "signin", "sign-in", "log-in",
    "signup", "sign-up", "register", "registration",
    "logout", "log-out",
    "forgot-password", "password-reset", "reset-password",
    "my-account", "account", "profile", "dashboard",
    "admin", "wp-admin", "wp-login.php", "wp-includes",
    # E-commerce transactional
    "cart", "checkout", "basket", "bag", "order-status", "wishlist",
    # Technical feeds & APIs
    "feed", "rss", "atom", "xmlrpc.php", "wp-json", "cdn-cgi",
})

# Minimum priority links threshold before examining general content
DEFAULT_MIN_PRIORITY_LINKS: int = 5


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class CandidateURL(BaseModel):
    """Represents a discovered and normalized candidate internal page URL."""

    url: str = Field(description="Normalized absolute URL of the candidate internal page.")
    link_text: str = Field(default="", description="Visible anchor text or accessible description.")
    page_type: PageType = Field(default=PageType.UNKNOWN, description="Preliminary classified PageType.")
    source_section: str = Field(
        default="content",
        description="HTML section where link was found ('header', 'nav', 'footer', or 'content').",
    )


class URLExtractionResult(BaseModel):
    """Structured result returned by the homepage URL extraction service."""

    base_url: str = Field(description="The base URL used to resolve relative links.")
    status: Literal["success", "empty_response", "blocked", "failed"] = Field(
        description="Outcome of the extraction process."
    )
    message: str | None = Field(
        default=None,
        description="Diagnostic or explanatory message explaining the outcome.",
    )
    candidate_urls: list[CandidateURL] = Field(
        default_factory=list,
        description="Deduplicated list of candidate URLs in deterministic priority order.",
    )
    total_found: int = Field(
        default=0,
        description="Total number of candidate URLs returned.",
    )


# ---------------------------------------------------------------------------
# Domain Comparison & URL Cleaning
# ---------------------------------------------------------------------------

def _is_same_website(candidate_netloc: str, base_netloc: str) -> bool:
    """Check if candidate host belongs to the same website as base host.

    Handles `www.` prefixes and internal subdomains (e.g. blog.example.com).
    """
    c_host = candidate_netloc.lower().split(":")[0]
    b_host = base_netloc.lower().split(":")[0]
    if not c_host or not b_host:
        return False

    clean_c = c_host[4:] if c_host.startswith("www.") else c_host
    clean_b = b_host[4:] if b_host.startswith("www.") else b_host

    if clean_c == clean_b:
        return True

    # Allow subdomains of the base website (e.g., store.example.com vs example.com)
    if clean_c.endswith("." + clean_b):
        return True

    return False


def _clean_and_resolve_url(raw_href: str, base_url: str) -> str | None:
    """Resolve a raw link against base_url, strip tracking params, and filter out invalid routes.

    Returns the canonical URL string if valid, or None if the link should be excluded.
    """
    cleaned = raw_href.strip()
    if not cleaned:
        return None

    # Ignore non-navigational links
    lower_href = cleaned.lower()
    if lower_href.startswith(("#", "mailto:", "tel:", "javascript:", "data:", "sms:", "callto:")):
        return None

    # Resolve relative URL against base_url
    try:
        resolved = urllib.parse.urljoin(base_url, cleaned)
    except Exception:
        return None

    parsed = urllib.parse.urlsplit(resolved)

    # Only accept HTTP and HTTPS
    if parsed.scheme.lower() not in ("http", "https"):
        return None

    if not parsed.netloc:
        return None

    # Ensure link belongs to the same business website
    base_parsed = urllib.parse.urlsplit(base_url)
    if not _is_same_website(parsed.netloc, base_parsed.netloc):
        return None

    # Normalize path
    raw_path = parsed.path or "/"
    # Collapse multiple slashes into single slash
    path = re.sub(r"/+", "/", raw_path)

    # Check static asset extensions
    path_lower = path.lower()
    for ext in _IGNORED_EXTENSIONS:
        if path_lower.endswith(ext):
            return None

    # Check excluded route segments
    segments = [seg.lower() for seg in path.strip("/").split("/") if seg]
    for seg in segments:
        if seg in _EXCLUDED_PATH_SEGMENTS:
            return None

    # Search endpoints (e.g., /search or query s=...)
    if segments and segments[-1] in ("search", "find"):
        return None

    # Clean query parameters: remove tracking / analytics params
    cleaned_query = ""
    if parsed.query:
        query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        filtered_pairs = [
            (k, v) for k, v in query_pairs
            if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith("utm_")
        ]
        if filtered_pairs:
            # Sort query parameters for deterministic deduplication
            filtered_pairs.sort(key=lambda x: x[0])
            cleaned_query = urllib.parse.urlencode(filtered_pairs)

    # Trailing slash normalization:
    # If path is not root and has no file extension (e.g. .html), strip trailing slash
    if path != "/" and "." not in path.split("/")[-1]:
        path = path.rstrip("/")

    # Reconstruct canonical URL (fragment is completely dropped)
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), path or "/", cleaned_query, "")
    )


# ---------------------------------------------------------------------------
# PageType Classification
# ---------------------------------------------------------------------------

def classify_page_type(url: str, link_text: str = "") -> PageType:
    """Assign a preliminary PageType enum based on URL path and anchor text keywords.

    Classification is heuristic and preliminary; actual content validation occurs
    in later pipeline stages.
    """
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path.lower().strip("/")
    text = (link_text or "").lower().strip()

    # 1. Home
    if not path or path in ("home", "index", "index.html", "index.php"):
        return PageType.HOME
    if text in ("home", "homepage", "main"):
        return PageType.HOME

    path_tokens = set(re.split(r"[-_/]", path))

    # 2. Policy (checked before others to prevent refund-policy matching offer or terms matching other)
    policy_keywords = {
        "privacy", "policy", "policies", "terms", "tos", "conditions",
        "refund", "refunds", "return", "returns", "shipping", "guarantee",
        "warranty", "legal", "disclaimer", "cookie", "cookies", "gdpr",
        "compliance", "accessibility",
    }
    if any(k in path_tokens for k in policy_keywords) or any(
        k in path for k in (
            "privacy-policy", "terms-of-service", "terms-and-conditions",
            "refund-policy", "return-policy", "shipping-policy", "cookie-policy"
        )
    ):
        return PageType.POLICY
    if any(k in text for k in (
        "privacy", "terms of", "terms &", "terms and", "refund",
        "return policy", "shipping policy", "legal", "disclaimer", "cookie policy"
    )):
        return PageType.POLICY

    # 3. Pricing
    pricing_keywords = {
        "pricing", "prices", "price", "plans", "packages", "rates",
        "subscription", "subscriptions", "fees", "cost", "tier", "tiers",
    }
    if any(k in path_tokens for k in pricing_keywords) or any(
        k in text for k in ("pricing", "plans", "rates", "subscription", "subscriptions", "packages")
    ):
        return PageType.PRICING

    # 4. FAQ
    faq_keywords = {"faq", "faqs", "help", "support", "questions", "answers"}
    if any(k in path_tokens for k in faq_keywords) or any(
        k in text for k in ("faq", "faqs", "frequently asked", "help center", "knowledge base")
    ):
        return PageType.FAQ

    # 5. About
    about_keywords = {
        "about", "team", "company", "story", "mission", "leadership",
        "careers", "press", "culture", "values", "executives", "directors",
    }
    if any(k in path_tokens for k in about_keywords) or any(
        k in path for k in ("about-us", "who-we-are", "our-story", "our-team", "our-company")
    ):
        return PageType.ABOUT
    if any(k in text for k in (
        "about", "who we are", "our story", "our team", "leadership", "company", "mission"
    )):
        return PageType.ABOUT

    # 6. Contact & Location
    contact_keywords = {
        "contact", "locations", "location", "reach", "quote", "appointment",
        "book", "booking", "schedule", "offices", "find-us", "demo", "request-demo",
    }
    if any(k in path_tokens for k in contact_keywords) or any(
        k in path for k in ("contact-us", "get-in-touch", "find-us", "request-quote", "book-online", "book-demo", "request-demo")
    ):
        return PageType.CONTACT
    if any(k in text for k in (
        "contact", "get in touch", "locations", "find us", "reach us", "book an appointment", "request a quote", "book demo", "request demo"
    )):
        return PageType.CONTACT

    # 7. Offer & Promotions
    offer_keywords = {
        "offer", "offers", "deals", "deal", "discount", "discounts",
        "promo", "promotions", "specials", "special", "coupons", "sale", "clearance",
    }
    if any(k in path_tokens for k in offer_keywords) or any(
        k in text for k in ("special offer", "deals", "discounts", "promotions", "sale", "clearance")
    ):
        return PageType.OFFER

    # 8. Product
    product_keywords = {
        "product", "products", "shop", "store", "collection", "collections",
        "catalog", "catalogue", "merchandise", "items", "goods",
        "features", "feature", "platform",
    }
    if any(k in path_tokens for k in product_keywords) or any(
        k in text for k in ("product", "products", "shop", "store", "collection", "catalog", "features", "platform")
    ):
        return PageType.PRODUCT

    # 9. Service
    service_keywords = {
        "service", "services", "solution", "solutions", "offerings",
        "capabilities", "treatments", "consulting", "practice", "practices",
        "use-case", "use-cases", "case-study", "case-studies",
    }
    if any(k in path_tokens for k in service_keywords) or any(
        k in text for k in ("service", "services", "solution", "solutions", "what we do", "capabilities", "use cases", "case studies")
    ):
        return PageType.SERVICE

    # 10. Blog & Insights
    blog_keywords = {
        "blog", "blogs", "news", "articles", "insights", "resources",
        "journal", "posts", "stories", "media", "press-release",
    }
    if any(k in path_tokens for k in blog_keywords) or any(
        k in text for k in ("blog", "news", "articles", "insights", "resources", "journal")
    ):
        return PageType.BLOG

    # 11. Other / Unknown
    if path:
        return PageType.OTHER

    return PageType.UNKNOWN


# ---------------------------------------------------------------------------
# Link Text Extraction
# ---------------------------------------------------------------------------

def _extract_link_text(a_tag: Tag) -> str:
    """Extract clean visible text or accessible label from an <a> tag."""
    # 1. Direct text inside anchor
    text = a_tag.get_text(separator=" ", strip=True)
    if text:
        return re.sub(r"\s+", " ", text).strip()

    # 2. aria-label, title, or alt attributes on anchor
    for attr in ("aria-label", "title"):
        val = a_tag.get(attr)
        if val and isinstance(val, str) and val.strip():
            return re.sub(r"\s+", " ", val).strip()

    # 3. Check for child image with alt text (e.g. logo or banner link)
    img = a_tag.find("img")
    if isinstance(img, Tag):
        alt = img.get("alt")
        if alt and isinstance(alt, str) and alt.strip():
            return re.sub(r"\s+", " ", alt).strip()

    return ""


# ---------------------------------------------------------------------------
# Core Extraction
# ---------------------------------------------------------------------------

def _extract_from_tags(
    anchors: list[Tag],
    base_url: str,
    section: str,
    seen_urls: set[str],
    include_home: bool = True,
) -> list[CandidateURL]:
    """Helper to process anchor tags into CandidateURL models."""
    extracted: list[CandidateURL] = []
    base_clean = base_url.rstrip("/")

    for a in anchors:
        raw_href = a.get("href")
        if not raw_href or not isinstance(raw_href, str):
            continue

        clean_url = _clean_and_resolve_url(raw_href, base_url)
        if not clean_url:
            continue

        # Optional check: skip homepage itself if include_home is False
        if not include_home and clean_url.rstrip("/") == base_clean:
            continue

        if clean_url in seen_urls:
            continue

        seen_urls.add(clean_url)
        link_text = _extract_link_text(a)
        page_type = classify_page_type(clean_url, link_text)

        extracted.append(
            CandidateURL(
                url=clean_url,
                link_text=link_text,
                page_type=page_type,
                source_section=section,
            )
        )

    return extracted


def extract_candidate_urls(
    homepage_data: HomepageFetchResult | dict[str, Any] | str,
    base_url: str | None = None,
    *,
    min_priority_links: int = DEFAULT_MIN_PRIORITY_LINKS,
    include_home: bool = True,
) -> URLExtractionResult:
    """Extract candidate internal website URLs from rendered homepage HTML.

    Inspects header, navigation, and footer menus first. If fewer than
    ``min_priority_links`` (default 5) unique links are discovered,
    also inspects links from the main content.

    Args:
        homepage_data: A :class:`HomepageFetchResult`, dict, or raw HTML string.
        base_url: Base URL to resolve relative links. Required if ``homepage_data`` is str.
        min_priority_links: Threshold below which content links will also be considered.
        include_home: Whether to include links pointing to the homepage itself.

    Returns:
        A populated :class:`URLExtractionResult` instance.
    """
    html: str | None = None
    resolved_base: str = ""

    # 1. Unpack input depending on type
    is_fetch_result = isinstance(homepage_data, HomepageFetchResult) or (
        type(homepage_data).__name__ == "HomepageFetchResult"
        and hasattr(homepage_data, "requested_url")
        and isinstance(getattr(homepage_data, "requested_url", None), str)
        and hasattr(homepage_data, "status")
        and getattr(homepage_data, "status", None) in ("success", "blocked", "empty_response", "failed")
        and hasattr(homepage_data, "html")
    )

    if is_fetch_result:
        resolved_base = base_url or homepage_data.final_url or homepage_data.requested_url

        if homepage_data.status == "failed":
            return URLExtractionResult(
                base_url=resolved_base,
                status="failed",
                message=f"Homepage fetch failed: {homepage_data.error_message}",
                candidate_urls=[],
                total_found=0,
            )

        if homepage_data.status == "blocked":
            return URLExtractionResult(
                base_url=resolved_base,
                status="blocked",
                message="Homepage response was blocked or CAPTCHA-gated; cannot extract URLs.",
                candidate_urls=[],
                total_found=0,
            )

        if homepage_data.status == "empty_response" or not homepage_data.html:
            return URLExtractionResult(
                base_url=resolved_base,
                status="empty_response",
                message="Homepage returned empty HTML; no URLs to extract.",
                candidate_urls=[],
                total_found=0,
            )

        html = homepage_data.html

    elif isinstance(homepage_data, dict):
        status = homepage_data.get("status", "success")
        req_url = homepage_data.get("requested_url", "")
        fin_url = homepage_data.get("final_url", "")
        resolved_base = base_url or fin_url or req_url or "https://unknown.local"

        if status == "failed":
            return URLExtractionResult(
                base_url=resolved_base,
                status="failed",
                message=f"Homepage fetch failed: {homepage_data.get('error_message')}",
                candidate_urls=[],
                total_found=0,
            )

        if status == "blocked":
            return URLExtractionResult(
                base_url=resolved_base,
                status="blocked",
                message="Homepage response was blocked or CAPTCHA-gated; cannot extract URLs.",
                candidate_urls=[],
                total_found=0,
            )

        raw_html = homepage_data.get("html")
        if status == "empty_response" or not raw_html:
            return URLExtractionResult(
                base_url=resolved_base,
                status="empty_response",
                message="Homepage returned empty HTML; no URLs to extract.",
                candidate_urls=[],
                total_found=0,
            )

        html = str(raw_html)

    elif isinstance(homepage_data, str):
        if not base_url:
            raise ValueError("base_url must be provided when homepage_data is a raw HTML string.")
        resolved_base = base_url
        html = homepage_data

    else:
        raise TypeError(
            f"Unsupported homepage_data type: {type(homepage_data).__name__}. "
            "Expected HomepageFetchResult, dict, or str."
        )

    # 2. Check for empty HTML content
    if not html or not html.strip():
        return URLExtractionResult(
            base_url=resolved_base,
            status="empty_response",
            message="Homepage HTML is empty; no URLs to extract.",
            candidate_urls=[],
            total_found=0,
        )

    # 3. Parse with BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    # Decompose script, style, and iframe tags
    for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()

    seen_urls: set[str] = set()
    candidate_urls: list[CandidateURL] = []

    # 4. Extract Header & Navigation links
    # Look for standard semantic elements and common navigation containers
    nav_elements = soup.find_all(
        lambda t: t.name in ("header", "nav")
        or (isinstance(t, Tag) and t.get("role") == "navigation")
        or (
            isinstance(t, Tag)
            and any(
                re.search(r"(?i)\b(header|nav|navbar|menu)\b", str(t.get(attr, "")))
                for attr in ("id", "class")
            )
        )
    )
    header_nav_anchors: list[Tag] = []
    for el in nav_elements:
        header_nav_anchors.extend(el.find_all("a"))

    nav_links = _extract_from_tags(
        header_nav_anchors,
        resolved_base,
        section="nav",
        seen_urls=seen_urls,
        include_home=include_home,
    )
    candidate_urls.extend(nav_links)

    # 5. Extract Footer links
    footer_elements = soup.find_all(
        lambda t: t.name == "footer"
        or (isinstance(t, Tag) and t.get("role") == "contentinfo")
        or (
            isinstance(t, Tag)
            and any(
                re.search(r"(?i)\b(footer|bottom)\b", str(t.get(attr, "")))
                for attr in ("id", "class")
            )
        )
    )
    footer_anchors: list[Tag] = []
    for el in footer_elements:
        footer_anchors.extend(el.find_all("a"))

    footer_links = _extract_from_tags(
        footer_anchors,
        resolved_base,
        section="footer",
        seen_urls=seen_urls,
        include_home=include_home,
    )
    candidate_urls.extend(footer_links)

    # 6. Fallback / supplement: If fewer than min_priority_links, inspect main page content
    if len(candidate_urls) < min_priority_links:
        logger.info(
            "Priority sections yielded %d links (< %d); inspecting main page content for %s.",
            len(candidate_urls),
            min_priority_links,
            resolved_base,
        )

        # Decompose already searched header, nav, and footer to isolate main content
        for el in nav_elements:
            el.decompose()
        for el in footer_elements:
            el.decompose()

        content_anchors = soup.find_all("a")
        content_links = _extract_from_tags(
            content_anchors,
            resolved_base,
            section="content",
            seen_urls=seen_urls,
            include_home=include_home,
        )
        candidate_urls.extend(content_links)

    total_count = len(candidate_urls)
    logger.info(
        "Extracted %d candidate internal URLs for %s (status: success).",
        total_count,
        resolved_base,
    )

    msg = None if total_count > 0 else "No candidate internal URLs found on page."
    return URLExtractionResult(
        base_url=resolved_base,
        status="success",
        message=msg,
        candidate_urls=candidate_urls,
        total_found=total_count,
    )
