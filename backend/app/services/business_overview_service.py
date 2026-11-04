"""Business Overview Generation Service.

Converts successfully crawled website pages into a concise, structured,
and strictly evidence-based business overview for downstream competitor analysis.

Key Responsibilities:
- Accept crawled content from :class:`WebsiteCrawlResult` or a sequence of :class:`ScrapedPage` models.
- Validate that sufficient, usable content exists before invoking any LLM.
- Ground all facts, products, services, features, and positioning in the provided website text.
- Enforce strict accuracy: no hallucinated facts, prices, audience claims, or marketing advice.
- Distinguish between explicit statements and inferred observations.
- Preserve source URLs on every item to ensure full traceability, including multi-page attribution.
- Support accurate offering classification: individual products, variants, collections, services, packages, SaaS features, and subscription plans.
- Support structured pricing: price, currency, original price, discounts, billing period, and availability status.
- Identify and catalog information gaps and explicit discrepancies between user intake and website content.
- Use the shared canonical model configuration (``settings.LLM_MODEL``).
- Bound token usage by applying deterministic water-filling per-page and total input character limits.
- Return a structured :class:`BusinessOverviewResult`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.parse
from typing import Any, Literal, Sequence

from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field

from backend.app.agent.prompts.business_overview import (
    BUSINESS_OVERVIEW_SYSTEM_PROMPT,
    BUSINESS_OVERVIEW_USER_PROMPT_TEMPLATE,
)
from backend.app.core.config import settings
from backend.app.schemas.enums import OfferingType, PageType, PriceAvailability
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.content_validation import evaluate_page_content_quality
from .website_content_crawler_service import WebsiteCrawlResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Operational & Cost Control Limits
# ---------------------------------------------------------------------------

# Minimum total characters required across all pages to warrant an LLM generation call
MIN_TOTAL_CONTENT_CHARS: int = 150
MIN_USABLE_PAGE_CHARS: int = 300

# Request timeout for OpenAI LLM call
DEFAULT_LLM_TIMEOUT_SECONDS: float = 60.0


# ---------------------------------------------------------------------------
# Structured Output Models
# ---------------------------------------------------------------------------

class PricingInfo(BaseModel):
    """Structured commercial pricing details for a product, service, collection, or plan."""

    model_config = ConfigDict(str_strip_whitespace=True)

    availability: PriceAvailability = Field(
        default=PriceAvailability.NOT_STATED,
        description="Availability status: 'available', 'quote_based', 'free_tier', or 'not_stated'.",
    )
    price: str | None = Field(
        default=None,
        description="Current published price as stated on the page, including currency symbol (e.g. '₹269', '$49').",
    )
    currency: str | None = Field(
        default=None,
        description="Currency code or symbol if explicitly stated (e.g. 'INR', 'USD', '₹', '$').",
    )
    original_price: str | None = Field(
        default=None,
        description="Original or struck-through price prior to discount if explicitly displayed on page (e.g. '₹399', '$65').",
    )
    discount_text: str | None = Field(
        default=None,
        description="Explicit discount, coupon, or promotional offer text (e.g. 'Coupon: ₹175', 'Save 20% with code GLOW').",
    )
    billing_period: str | None = Field(
        default=None,
        description="Billing cycle or cadence if applicable (e.g. 'monthly', 'annual', 'one-time', 'per session', 'per user/month').",
    )
    trial_or_guarantee: str | None = Field(
        default=None,
        description="Free trial duration, money-back guarantee, or commercial warranty terms if explicitly stated (e.g. '14-day free trial', '30-day money-back guarantee').",
    )


class ProductServiceItem(BaseModel):
    """Represents a discrete product category, service line, collection, or core offering."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(description="Name or title of the product category, service line, collection, or core offering.")
    offering_type: OfferingType = Field(
        default=OfferingType.COLLECTION,
        description="Classification: 'collection', 'product', 'service', 'service_package', 'saas_feature', 'subscription_plan', 'bundle', or 'other'.",
    )
    description: str = Field(description="Informative description explaining what the category contains or service provides, citing illustrative examples if relevant.")
    pricing: PricingInfo | None = Field(
        default=None,
        description="High-level commercial pricing model or price tier if supported by website evidence.",
    )
    variants: list[str] = Field(
        default_factory=list,
        description="Key subcategories, product lines, or illustrative items within this offering category.",
    )
    features: list[str] = Field(
        default_factory=list,
        description="Key features or capabilities explicitly mentioned on the page.",
    )
    benefits: list[str] = Field(
        default_factory=list,
        description="Target benefits or value delivered to customers as claimed by the business.",
    )
    source_url: str | None = Field(
        default=None,
        description="Specific page URL providing evidence for this offering, or None if unavailable or unverified.",
    )
    source_urls: list[str] = Field(
        default_factory=list,
        description="List of verified page URLs providing evidence for this offering.",
    )


class TargetAudienceInfo(BaseModel):
    """Target customer profile or audience segment supported by website evidence."""

    model_config = ConfigDict(str_strip_whitespace=True)

    description: str = Field(
        description="Description of the intended audience, or 'Not specified on website' if unknown."
    )
    is_explicit: bool = Field(
        description="True if explicitly identified (e.g. 'Built for B2B sales teams'); False if inferred or unknown."
    )
    source_url: str | None = Field(
        default=None,
        description="Page URL where target audience evidence was found, or None if unknown.",
    )
    user_stated_audience: str | None = Field(
        default=None,
        description="User-provided target audience from business intake form if supplied.",
    )
    has_conflict: bool = Field(
        default=False,
        description="True if website evidence conflicts with user-stated audience.",
    )


class ValueProposition(BaseModel):
    """A distinct positioning statement or core value proposition."""

    model_config = ConfigDict(str_strip_whitespace=True)

    statement: str = Field(description="The positioning claim or value proposition.")
    is_explicit: bool = Field(
        description="True if directly claimed on the website; False if synthesized from content."
    )
    source_url: str | None = Field(
        default=None,
        description="Page URL supporting this value proposition, or None if unavailable or unverified.",
    )
    source_urls: list[str] = Field(
        default_factory=list,
        description="List of verified page URLs supporting this value proposition.",
    )


class InformationGap(BaseModel):
    """Important business information that could not be determined from the available pages."""

    model_config = ConfigDict(str_strip_whitespace=True)

    category: str = Field(
        description="Category of undetermined info (e.g. 'Pricing & Plans', 'Target Customer Size', 'Founders/Team')."
    )
    observation: str = Field(
        description="Factual note explaining what could not be determined from the crawled pages."
    )


class BusinessOverview(BaseModel):
    """Complete evidence-based business overview generated from crawled website content."""

    model_config = ConfigDict(str_strip_whitespace=True)

    business_name: str | None = Field(
        default=None,
        description="Company or business name extracted from page titles and content.",
    )
    website_url: str = Field(description="Primary website URL analyzed.")
    business_description: str = Field(
        description="Clear, factual overview summarizing what the company does and its primary domain."
    )
    products_and_services: list[ProductServiceItem] = Field(
        default_factory=list,
        description="Primary products, services, collections, features, or plans offered by the business.",
    )
    target_audience: TargetAudienceInfo = Field(
        description="Target customer or audience analysis with explicit vs inferred distinction.",
    )
    positioning_and_value_propositions: list[ValueProposition] = Field(
        default_factory=list,
        description="Key positioning claims, value propositions, or differentiators.",
    )
    conflicts: list[str] = Field(
        default_factory=list,
        description="Explicit discrepancies identified between user intake and website evidence.",
    )
    information_gaps: list[InformationGap] = Field(
        default_factory=list,
        description="Key business dimensions that could not be determined from the available crawled content.",
    )
    sources_used: list[str] = Field(
        default_factory=list,
        description="List of all distinct page URLs that contributed evidence to this overview.",
    )


class BusinessOverviewResult(BaseModel):
    """Complete structured result for a business overview generation request."""

    model_config = ConfigDict(str_strip_whitespace=True)

    status: Literal["success", "insufficient_content", "failed"] = Field(
        description="Outcome of the overview generation: 'success', 'insufficient_content', or 'failed'."
    )
    business_overview: BusinessOverview | None = Field(
        default=None,
        description="Generated business overview if status is 'success', else None.",
    )
    website_url: str = Field(description="Target website URL.")
    total_pages_analyzed: int = Field(
        default=0,
        description="Count of crawled pages with readable content submitted to the analysis.",
    )
    total_findings: int = Field(
        default=0,
        description="Count of key structured findings extracted.",
    )
    model_used: str | None = Field(
        default=None,
        description="LLM model identifier used for analysis (defaults to settings.LLM_MODEL).",
    )
    execution_time_seconds: float = Field(
        default=0.0,
        description="Total duration in seconds.",
    )
    error_message: str | None = Field(
        default=None,
        description="Detailed explanation if status is 'insufficient_content' or 'failed'.",
    )





# ---------------------------------------------------------------------------
# Helper Functions: Page Extraction & Fair Budget Allocation
# ---------------------------------------------------------------------------

def _extract_usable_pages(
    crawl_input: WebsiteCrawlResult | Sequence[ScrapedPage],
) -> tuple[list[ScrapedPage], str]:
    """Extract and validate non-empty ScrapedPage models and infer the base website URL.

    Filters out empty pages with zero readable text content. Strips boilerplate notices.

    Returns:
        (usable_pages, website_url)
    """
    from backend.app.services.content_validation import clean_boilerplate

    usable_pages: list[ScrapedPage] = []
    website_url: str = ""

    raw_pages: list[ScrapedPage] = []
    if isinstance(crawl_input, WebsiteCrawlResult):
        website_url = crawl_input.website_url
        raw_pages = list(crawl_input.scraped_pages)
    elif isinstance(crawl_input, Sequence):
        for item in crawl_input:
            if isinstance(item, ScrapedPage):
                raw_pages.append(item)
                if not website_url and item.url:
                    parsed = urllib.parse.urlsplit(item.url)
                    website_url = f"{parsed.scheme}://{parsed.netloc}"

    for page in raw_pages:
        if page.content and page.content.strip():
            cleaned = clean_boilerplate(page.content)
            # Use cleaned content if not empty, otherwise preserve original
            if cleaned:
                page.content = cleaned
                usable_pages.append(page)
            elif page.content.strip():
                usable_pages.append(page)

    return usable_pages, website_url


def _format_pages_for_prompt(
    pages: list[ScrapedPage],
) -> tuple[str, list[ScrapedPage]]:
    """Format complete scraped page content for the LLM prompt.

    Separates full substantive pages from weak-signal pages (< MIN_USABLE_PAGE_CHARS)
    to prevent hallucinations while preserving all recovered context.

    Returns:
        (formatted_pages_text, included_pages)
    """
    if not pages:
        return "", []

    usable = [p for p in pages if p.content and p.content.strip()]
    if not usable:
        return "", []

    min_usable = 300
    try:
        min_usable = int(getattr(settings, "MIN_USABLE_PAGE_CHARS", 300))
    except (TypeError, ValueError):
        min_usable = 300

    formatted_chunks: list[str] = []
    included_pages: list[ScrapedPage] = []

    primary_pages: list[tuple[int, ScrapedPage]] = []
    weak_signal_pages: list[tuple[int, ScrapedPage, str]] = []
    accumulated_texts: list[str] = []

    for idx, page in enumerate(usable, start=1):
        quality, reason = evaluate_page_content_quality(
            page.content,
            page_type=page.page_type,
            existing_texts=accumulated_texts,
            url=page.url or "",
        )
        if quality == "primary":
            primary_pages.append((idx, page))
            accumulated_texts.append(page.content)
        else:
            weak_signal_pages.append((idx, page, reason))
            if len(page.content.strip()) >= 100:
                accumulated_texts.append(page.content)

    # 1. Format full primary pages
    for idx, page in primary_pages:
        title_str = f" | Title: {page.title.strip()}" if page.title and page.title.strip() else ""
        type_str = f" | Type: {page.page_type.value}" if hasattr(page.page_type, "value") else ""
        header = f"--- Page {idx}: {page.url}{title_str}{type_str} ---"

        page_text = page.content.strip()
        chunk = f"{header}\n{page_text}\n"
        formatted_chunks.append(chunk)
        included_pages.append(page)

    # 2. Format weak-signal pages (partial / duplicate / limited content)
    for idx, page, reason in weak_signal_pages:
        title_str = f" | Title: {page.title.strip()}" if page.title and page.title.strip() else ""
        type_str = f" | Type: {page.page_type.value}" if hasattr(page.page_type, "value") else ""
        header = f"--- Page {idx}: {page.url}{title_str}{type_str} ---"

        page_text = page.content.strip()
        warning = f"[Weak Signal: Partial content ({reason}). Treat as limited evidence; do not extrapolate missing facts.]"
        chunk = f"{header}\n{warning}\n{page_text}\n"
        formatted_chunks.append(chunk)
        included_pages.append(page)

    final_formatted = "\n".join(formatted_chunks)

    logger.info(
        "[business_overview_trace] [CHECKPOINT 2: format_pages] Formatted prompt: included=%d/%d pages (%d chars, primary=%d, weak=%d).",
        len(included_pages),
        len(pages),
        len(final_formatted),
        len(primary_pages),
        len(weak_signal_pages),
    )

    return final_formatted, included_pages


# ---------------------------------------------------------------------------
# Regional / Geo Context Detection & Emergency Synthesis
# ---------------------------------------------------------------------------

_REGIONAL_PREFIX_MAP: dict[str, str] = {
    "en-us": "United States (US)",
    "us": "United States (US)",
    "en-in": "India (IN)",
    "in": "India (IN)",
    "en-gb": "United Kingdom (UK)",
    "uk": "United Kingdom (UK)",
    "gb": "United Kingdom (UK)",
    "en-ca": "Canada (CA)",
    "ca": "Canada (CA)",
    "en-au": "Australia (AU)",
    "au": "Australia (AU)",
    "en-de": "Germany (DE)",
    "de": "Germany (DE)",
    "en-fr": "France (FR)",
    "fr": "France (FR)",
}

_REGIONAL_TLD_MAP: dict[str, str] = {
    ".in": "India (IN)",
    ".co.in": "India (IN)",
    ".co.uk": "United Kingdom (UK)",
    ".ca": "Canada (CA)",
    ".com.au": "Australia (AU)",
    ".au": "Australia (AU)",
    ".de": "Germany (DE)",
    ".fr": "France (FR)",
}


def detect_regional_context(
    website_url: str,
    pages: Sequence[ScrapedPage],
    business_form: Any = None,
) -> str | None:
    """Detect regional market/geo context from URL path prefixes or ccTLDs.

    Distinguishes proxy-induced /en-us redirects from genuine target markets by
    cross-referencing with user intake data.
    """
    all_urls = [website_url] + [p.url for p in pages if p.url]

    # Extract user intake regional indicators
    intake_text = ""
    if business_form:
        if hasattr(business_form, "business_name_and_description"):
            intake_text = f"{getattr(business_form, 'business_name_and_description', '')} {getattr(business_form, 'target_audience', '')} {getattr(business_form, 'product_or_service', '')}".lower()
        elif isinstance(business_form, dict):
            intake_text = f"{business_form.get('business_name_and_description', '')} {business_form.get('target_audience', '')} {business_form.get('product_or_service', '')}".lower()

    user_is_india = any(
        kw in intake_text
        for kw in ("india", "indian", "inr", "₹", "delhi", "mumbai", "bangalore", "bengaluru", "gurgaon", "hyderabad", "chennai")
    )
    domain_is_india = any(website_url.lower().endswith(t) for t in (".in", ".co.in")) or ".in/" in website_url.lower()

    # Check path prefixes first (e.g. /en-us/, /in/)
    for u in all_urls:
        try:
            parsed = urllib.parse.urlsplit(u)
            path_parts = [seg.lower() for seg in parsed.path.split("/") if seg]
            if path_parts:
                first_seg = path_parts[0]
                if first_seg in _REGIONAL_PREFIX_MAP:
                    region_desc = _REGIONAL_PREFIX_MAP[first_seg]
                    if (user_is_india or domain_is_india) and first_seg in ("en-us", "us"):
                        return (
                            "Regional / Geo Context: Website redirected to /en-us (observed scraper proxy redirect), "
                            "while user business profile indicates India / domestic target market. Prioritize the user "
                            "business profile for primary market, currency (INR/₹), and domestic audience, while noting "
                            "the /en-us international storefront."
                        )
                    return (
                        f"Regional / Geo Context: Website content indicates targeting for {region_desc} "
                        f"(detected '{first_seg}' in URL path). Take this into account for pricing currency, "
                        "market availability, and target audience."
                    )
        except Exception:
            continue

    # Check ccTLD
    for u in all_urls:
        try:
            parsed = urllib.parse.urlsplit(u)
            netloc = parsed.netloc.lower()
            for tld, region_desc in _REGIONAL_TLD_MAP.items():
                if netloc.endswith(tld):
                    return (
                        f"Regional / Geo Context: Website domain indicates targeting for {region_desc} "
                        f"(detected ccTLD '{tld}'). Take this into account for pricing currency, "
                        "market availability, and target audience."
                    )
        except Exception:
            continue

    return None


def synthesize_emergency_overview(
    business_form: Any,
    website_url: str,
    reason: str = "Crawled content was insufficient or blocked.",
) -> BusinessOverview:
    """Synthesize a best-effort emergency overview strictly from user intake when website crawling yields insufficient content."""
    name_desc = ""
    product = ""
    audience = ""
    if hasattr(business_form, "business_name_and_description"):
        name_desc = business_form.business_name_and_description or ""
        product = business_form.product_or_service or ""
        audience = business_form.target_audience or ""
    elif isinstance(business_form, dict):
        name_desc = business_form.get("business_name_and_description", "")
        product = business_form.get("product_or_service", "")
        audience = business_form.get("target_audience", "")

    # Derive name
    business_name = name_desc.split(" - ")[0].split(". ")[0].strip() if name_desc else "Business"
    if len(business_name) > 60:
        business_name = business_name[:60]

    products: list[ProductServiceItem] = []
    if product:
        products.append(
            ProductServiceItem(
                name=product,
                offering_type=OfferingType.PRODUCT,
                description=f"Primary product or service identified from user intake: {product}",
                source_url=None,
                source_urls=[],
            )
        )

    target_aud = TargetAudienceInfo(
        description=audience or "Target audience derived from business intake; website verification unavailable.",
        is_explicit=False,
        source_url=None,
        user_stated_audience=audience or None,
        has_conflict=False,
    )

    value_props: list[ValueProposition] = []
    if name_desc:
        value_props.append(
            ValueProposition(
                statement=name_desc,
                is_explicit=False,
                source_url=None,
                source_urls=[],
            )
        )

    gaps = [
        InformationGap(
            category="Website Content Acquisition",
            observation=f"Detailed website pages could not be analyzed ({reason}). Overview synthesized with limited confidence from business intake.",
        )
    ]

    return BusinessOverview(
        business_name=business_name,
        website_url=website_url or "https://unknown.com",
        business_description=name_desc or f"Business operating at {website_url}.",
        products_and_services=products,
        target_audience=target_aud,
        positioning_and_value_propositions=value_props,
        conflicts=[f"Emergency synthesis: {reason}"],
        information_gaps=gaps,
        sources_used=[],
    )


# ---------------------------------------------------------------------------
# Robust URL Normalization & Source Verification
# ---------------------------------------------------------------------------

def _normalize_url_for_comparison(url: str) -> str:
    """Normalize a URL for robust comparison across schemes, www, trailing slashes, and tracking query params."""
    cleaned = url.strip()
    if not cleaned:
        return ""
    try:
        parsed = urllib.parse.urlsplit(cleaned)
        scheme = parsed.scheme.lower() or "https"
        netloc = parsed.netloc.lower()
        if ":" in netloc:
            host, port = netloc.split(":", 1)
            if (scheme == "http" and port == "80") or (scheme == "https" and port == "443"):
                netloc = host
        if netloc.startswith("www."):
            netloc = netloc[4:]

        path = parsed.path or "/"
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")

        query = parsed.query
        if query:
            q_pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
            clean_q = [
                (k, v) for k, v in q_pairs
                if not k.lower().startswith("utm_") and k.lower() not in ("ref", "fbclid", "gclid")
            ]
            if clean_q:
                clean_q.sort(key=lambda x: x[0])
                query = urllib.parse.urlencode(clean_q)
            else:
                query = ""

        return urllib.parse.urlunsplit(("https", netloc, path, query, ""))
    except Exception:
        return cleaned.lower().rstrip("/")


def _resolve_and_verify_source_url(
    raw_url: str | None,
    norm_to_original: dict[str, str],
    path_to_original: dict[str, str],
    base_website_url: str,
) -> str | None:
    """Validate and resolve a source URL against the set of actually supplied pages.

    Returns the exact original crawled URL if matched, or None if the URL was not
    supplied to the LLM, is external, or is invalid. NEVER falls back to the homepage.
    """
    if not raw_url or not isinstance(raw_url, str):
        return None

    cleaned = raw_url.strip()
    if not cleaned:
        return None

    parsed = urllib.parse.urlsplit(cleaned)
    if not parsed.scheme or not parsed.netloc:
        cleaned_joined = urllib.parse.urljoin(base_website_url, cleaned)
        parsed = urllib.parse.urlsplit(cleaned_joined)
    else:
        cleaned_joined = cleaned

    if parsed.scheme.lower() not in ("http", "https"):
        return None

    base_host = urllib.parse.urlsplit(base_website_url).netloc.lower()
    cited_host = parsed.netloc.lower()
    if ":" in base_host:
        base_host = base_host.split(":")[0]
    if ":" in cited_host:
        cited_host = cited_host.split(":")[0]

    clean_base_host = base_host[4:] if base_host.startswith("www.") else base_host
    clean_cited_host = cited_host[4:] if cited_host.startswith("www.") else cited_host

    if (
        clean_cited_host != clean_base_host
        and not clean_cited_host.endswith("." + clean_base_host)
        and not clean_base_host.endswith("." + clean_cited_host)
    ):
        return None

    # 1. Direct normalized lookup
    norm = _normalize_url_for_comparison(cleaned_joined)
    if norm in norm_to_original:
        return norm_to_original[norm]

    # 2. Path-only lookup without query parameters
    norm_no_query = _normalize_url_for_comparison(
        urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", ""))
    )
    if norm_no_query in norm_to_original:
        return norm_to_original[norm_no_query]

    # 3. Relative path lookup against known paths
    clean_path = parsed.path.lower().rstrip("/") or "/"
    if clean_path in path_to_original:
        return path_to_original[clean_path]

    # 4. Fallback check for trimmed slug
    path_parts = [p for p in clean_path.split("/") if p]
    if path_parts:
        slug = "/" + path_parts[-1]
        if slug in path_to_original:
            return path_to_original[slug]

    return None


def _sanitize_and_validate_overview(
    overview: BusinessOverview,
    valid_source_urls: Sequence[str] | set[str] | Sequence[ScrapedPage],
    base_website_url: str | None = None,
    fallback_website_url: str | None = None,
    business_form: Any = None,
) -> BusinessOverview:
    """Verify and normalize source URLs against the pages actually supplied to the LLM.

    Strict Attribution Rules:
    - Never replace an un-crawled, invalid, or external source URL with the homepage.
    - If an offering or value proposition source URL is invalid or un-crawled,
      mark its source_url as None and clear invalid URLs from source_urls.
    - If a target audience or value proposition claim had is_explicit=True but its source
      cannot be verified, set is_explicit=False.
    - sources_used is compiled strictly from verified, non-null source URLs.
    - Preserves user-provided business intake and reports explicit contradictions in conflicts.
    """
    # Support callers passing business_form as base_website_url positionally
    if hasattr(base_website_url, "business_website") or hasattr(base_website_url, "business_name_and_description"):
        if business_form is None:
            business_form = base_website_url
        base_website_url = getattr(business_form, "business_website", None)

    resolved_base = base_website_url or fallback_website_url or overview.website_url or "https://unknown.com"
    if not overview.website_url:
        overview.website_url = resolved_base

    # Build canonical lookup tables from supplied pages
    norm_to_original: dict[str, str] = {}
    path_to_original: dict[str, str] = {}

    for item in valid_source_urls:
        url_str = item.url if isinstance(item, ScrapedPage) else item
        if url_str and isinstance(url_str, str):
            orig_url = url_str.strip()
            norm = _normalize_url_for_comparison(orig_url)
            norm_to_original[norm] = orig_url

            parsed_u = urllib.parse.urlsplit(orig_url)
            norm_no_q = _normalize_url_for_comparison(
                urllib.parse.urlunsplit((parsed_u.scheme.lower(), parsed_u.netloc.lower(), parsed_u.path, "", ""))
            )
            norm_to_original[norm_no_q] = orig_url

            clean_p = parsed_u.path.lower().rstrip("/") or "/"
            path_to_original[clean_p] = orig_url
            path_parts = [p for p in clean_p.split("/") if p]
            if path_parts:
                path_to_original["/" + path_parts[-1]] = orig_url

    # 1. Sanitize product/service items, pricing, and multi-source URLs
    unique_products: list[ProductServiceItem] = []
    seen_offering_names: set[str] = set()

    for p in overview.products_and_services:
        name_clean = p.name.strip() if p.name else ""
        name_norm = " ".join(name_clean.lower().split())
        if name_norm and name_norm in seen_offering_names:
            logger.info("Dropping duplicate offering category: '%s'", name_clean)
            continue
        if name_norm:
            seen_offering_names.add(name_norm)

        verified_sources_list: list[str] = []
        # Check source_urls list first
        if hasattr(p, "source_urls") and p.source_urls:
            for s_url in p.source_urls:
                ver = _resolve_and_verify_source_url(s_url, norm_to_original, path_to_original, resolved_base)
                if ver and ver not in verified_sources_list:
                    verified_sources_list.append(ver)

        # Check primary source_url
        verified_primary = _resolve_and_verify_source_url(
            p.source_url, norm_to_original, path_to_original, resolved_base
        )
        if verified_primary and verified_primary not in verified_sources_list:
            verified_sources_list.insert(0, verified_primary)

        p.source_urls = verified_sources_list
        p.source_url = verified_sources_list[0] if verified_sources_list else None

        # Sanitize pricing availability consistency
        if p.pricing is not None:
            if not p.pricing.price and p.pricing.availability == PriceAvailability.AVAILABLE:
                p.pricing.availability = PriceAvailability.NOT_STATED

        unique_products.append(p)

    overview.products_and_services = unique_products

    # 2. Sanitize value propositions
    for vp in overview.positioning_and_value_propositions:
        verified_vp_sources: list[str] = []
        if hasattr(vp, "source_urls") and vp.source_urls:
            for s_url in vp.source_urls:
                ver = _resolve_and_verify_source_url(s_url, norm_to_original, path_to_original, resolved_base)
                if ver and ver not in verified_vp_sources:
                    verified_vp_sources.append(ver)

        verified_primary = _resolve_and_verify_source_url(
            vp.source_url, norm_to_original, path_to_original, resolved_base
        )
        if verified_primary and verified_primary not in verified_vp_sources:
            verified_vp_sources.insert(0, verified_primary)

        vp.source_urls = verified_vp_sources
        vp.source_url = verified_vp_sources[0] if verified_vp_sources else None
        if not vp.source_url and vp.is_explicit:
            vp.is_explicit = False

    # 3. Sanitize target audience
    verified_audience_source = _resolve_and_verify_source_url(
        overview.target_audience.source_url, norm_to_original, path_to_original, resolved_base
    )
    overview.target_audience.source_url = verified_audience_source
    if verified_audience_source is None and overview.target_audience.is_explicit:
        overview.target_audience.is_explicit = False

    # 4. Compile sources_used strictly from verified sources
    verified_sources: set[str] = set()
    for p in overview.products_and_services:
        if p.source_url:
            verified_sources.add(p.source_url)
        for u in p.source_urls:
            verified_sources.add(u)
    for vp in overview.positioning_and_value_propositions:
        if vp.source_url:
            verified_sources.add(vp.source_url)
        for u in vp.source_urls:
            verified_sources.add(u)
    if overview.target_audience and overview.target_audience.source_url:
        verified_sources.add(overview.target_audience.source_url)

    # Check any explicit sources cited in overview.sources_used
    for cited in overview.sources_used:
        ver = _resolve_and_verify_source_url(cited, norm_to_original, path_to_original, resolved_base)
        if ver:
            verified_sources.add(ver)

    overview.sources_used = sorted(list(verified_sources))

    # 5. Harmonize with user-provided intake data and detect explicit conflicts
    if business_form is not None:
        stated_name_desc = ""
        stated_audience = ""
        audience_unknown = False

        if hasattr(business_form, "business_name_and_description"):
            stated_name_desc = business_form.business_name_and_description
            stated_audience = business_form.target_audience or ""
            audience_unknown = business_form.audience_unknown
        elif isinstance(business_form, dict):
            stated_name_desc = business_form.get("business_name_and_description", "")
            stated_audience = business_form.get("target_audience", "") or ""
            audience_unknown = bool(business_form.get("audience_unknown", False))

        if stated_audience and not audience_unknown:
            overview.target_audience.user_stated_audience = stated_audience
            # If website description is empty or explicitly unstated, set to user stated audience
            if not overview.target_audience.description or "not specified" in overview.target_audience.description.lower():
                overview.target_audience.description = stated_audience
            else:
                # Both exist: check for substantive conflict
                site_aud_low = overview.target_audience.description.lower()
                stated_aud_low = stated_audience.lower()
                # If site audience contradicts stated audience (e.g. B2B vs Teen Consumers)
                if (
                    ("b2b" in stated_aud_low and "b2c" in site_aud_low)
                    or ("enterprise" in stated_aud_low and "individual" in site_aud_low)
                ):
                    overview.target_audience.has_conflict = True
                    conflict_note = f"Audience discrepancy: User stated '{stated_audience}', but website indicates '{overview.target_audience.description}'."
                    if conflict_note not in overview.conflicts:
                        overview.conflicts.append(conflict_note)
            overview.target_audience.is_explicit = True

        # Preserve business description from intake if overview is empty
        if not overview.business_description or overview.business_description.strip() in ("", "unknown"):
            overview.business_description = stated_name_desc

    logger.info(
        "[business_overview_trace] [CHECKPOINT 5: sanitization] Sanitization complete: %d verified sources in sources_used, %d conflicts detected.",
        len(overview.sources_used),
        len(overview.conflicts),
    )

    return overview


# ---------------------------------------------------------------------------
# Public Service API
# ---------------------------------------------------------------------------

async def generate_business_overview(
    crawl_input: WebsiteCrawlResult | Sequence[ScrapedPage],
    *,
    website_url: str | None = None,
    business_form: Any = None,
    model: str | None = None,
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    on_progress: Any = None,
) -> BusinessOverviewResult:
    """Generate a structured, evidence-based business overview from crawled website content.

    Args:
        crawl_input: The crawled website output, either a :class:`WebsiteCrawlResult`
            or a sequence of :class:`ScrapedPage` instances.
        website_url: Optional root website URL. Inferred from crawl_input if omitted.
        business_form: Optional BusinessForm intake model or dict.
        model: Optional LLM model identifier. Defaults to shared configuration (settings.LLM_MODEL).
        timeout_seconds: Maximum seconds to wait for LLM completion.

    Returns:
        A populated :class:`BusinessOverviewResult` containing the overview and metadata.
    """
    start_time = time.perf_counter()

    async def _emit_progress(stage: str, message: str, meta: dict[str, Any] | None = None) -> None:
        if on_progress:
            try:
                res = on_progress(stage, message, meta or {})
                if asyncio.iscoroutine(res):
                    await res
            except Exception as prog_exc:
                logger.warning("Progress callback exception: %s", prog_exc, exc_info=True)

    # 1. Extract and validate usable scraped pages
    usable_pages, inferred_url = _extract_usable_pages(crawl_input)
    resolved_website_url = website_url or inferred_url or "https://unknown.com"

    logger.info(
        "[business_overview_trace] [CHECKPOINT 1: usable_pages] Extracted %d usable pages from crawl input for %s",
        len(usable_pages),
        resolved_website_url,
    )
    for idx, p in enumerate(usable_pages, start=1):
        p_content = p.content or ""
        logger.info(
            "[business_overview_trace] [CHECKPOINT 1: usable_pages] Page %d/%d: url=%s, chars=%d",
            idx,
            len(usable_pages),
            p.url,
            len(p_content.strip()),
        )

    # 2. Check for insufficient content
    total_content_length = sum(len(p.content.strip()) for p in usable_pages)
    if not usable_pages or total_content_length < MIN_TOTAL_CONTENT_CHARS:
        msg = (
            f"Insufficient readable website content ({total_content_length} chars across "
            f"{len(usable_pages)} pages; minimum required is {MIN_TOTAL_CONTENT_CHARS})."
        )
        enable_emergency = getattr(settings, "ENABLE_EMERGENCY_OVERVIEW", False)
        if isinstance(enable_emergency, bool) and enable_emergency and business_form:
            logger.info("Triggering emergency overview synthesis from user intake context.")
            emergency_overview = synthesize_emergency_overview(
                business_form=business_form,
                website_url=resolved_website_url,
                reason=msg,
            )
            return BusinessOverviewResult(
                status="success",
                business_overview=emergency_overview,
                website_url=resolved_website_url,
                total_pages_analyzed=len(usable_pages),
                total_findings=len(emergency_overview.products_and_services) + len(emergency_overview.positioning_and_value_propositions),
                model_used="emergency_fallback",
                execution_time_seconds=round(time.perf_counter() - start_time, 2),
                error_message=f"Synthesized emergency overview: {msg}",
            )

        logger.warning(
            "Cannot generate business overview for %s: %s",
            resolved_website_url,
            msg,
        )
        return BusinessOverviewResult(
            status="insufficient_content",
            business_overview=None,
            website_url=resolved_website_url,
            total_pages_analyzed=len(usable_pages),
            total_findings=0,
            model_used=None,
            execution_time_seconds=round(time.perf_counter() - start_time, 2),
            error_message=msg,
        )

    await _emit_progress(
        "extracting_info",
        f"Extracting offerings and value proposition from {len(usable_pages)} page(s)...",
        {"page_count": len(usable_pages), "total_chars": total_content_length},
    )

    # 3. Resolve LLM model identifier (defaults to shared settings.LLM_MODEL)
    resolved_model = model or settings.LLM_MODEL

    # 4. Validate OpenAI API key
    if not settings.OPENAI_API_KEY:
        err = "OPENAI_API_KEY is not configured in settings."
        logger.error(err)
        return BusinessOverviewResult(
            status="failed",
            business_overview=None,
            website_url=resolved_website_url,
            total_pages_analyzed=len(usable_pages),
            total_findings=0,
            model_used=resolved_model,
            execution_time_seconds=round(time.perf_counter() - start_time, 2),
            error_message=err,
        )

    raw_api_key = settings.OPENAI_API_KEY.get_secret_value().strip()
    if not raw_api_key or raw_api_key in ("your_openai_api_key_here", ""):
        err = "OPENAI_API_KEY is set to a placeholder value."
        logger.error(err)
        return BusinessOverviewResult(
            status="failed",
            business_overview=None,
            website_url=resolved_website_url,
            total_pages_analyzed=len(usable_pages),
            total_findings=0,
            model_used=resolved_model,
            execution_time_seconds=round(time.perf_counter() - start_time, 2),
            error_message=err,
        )

    # 5. Build prompt with complete page content and user intake context
    stated_name_desc = ""
    stated_product = ""
    stated_audience = ""
    audience_unknown = False

    if hasattr(business_form, "business_name_and_description"):
        stated_name_desc = business_form.business_name_and_description
        stated_product = business_form.product_or_service
        stated_audience = business_form.target_audience or ""
        audience_unknown = business_form.audience_unknown
    elif isinstance(business_form, dict):
        stated_name_desc = business_form.get("business_name_and_description", "")
        stated_product = business_form.get("product_or_service", "")
        stated_audience = business_form.get("target_audience", "") or ""
        audience_unknown = bool(business_form.get("audience_unknown", False))

    await _emit_progress(
        "identifying_audience",
        "Identifying target customers and audience profiles...",
        {"has_stated_audience": bool(stated_audience)},
    )

    intake_parts: list[str] = []
    if stated_name_desc:
        intake_parts.append(f"- Stated Business Identity: {stated_name_desc}")
    if stated_product:
        intake_parts.append(f"- Primary Product / Service Focus: {stated_product}")
    if stated_audience:
        intake_parts.append(f"- Stated Target Audience: {stated_audience}")
    elif audience_unknown:
        intake_parts.append("- Stated Target Audience: Not specified by user (audience_unknown=True)")

    enable_geo = getattr(settings, "ENABLE_REGIONAL_CONTEXT_DETECTION", True)
    if isinstance(enable_geo, bool) and enable_geo:
        geo_note = detect_regional_context(resolved_website_url, usable_pages, business_form=business_form)
        if geo_note:
            intake_parts.append(f"- {geo_note}")

    user_intake_context = "\n".join(intake_parts) if intake_parts else "No user intake provided; derive strictly from crawled content."
    product_focus = stated_product if stated_product else "Core commercial offerings"

    formatted_pages, included_pages = _format_pages_for_prompt(usable_pages)
    user_prompt = BUSINESS_OVERVIEW_USER_PROMPT_TEMPLATE.format(
        website_url=resolved_website_url,
        user_intake_context=user_intake_context,
        product_focus=product_focus,
        page_count=len(included_pages),
        formatted_pages=formatted_pages,
    )

    logger.info(
        "[business_overview_trace] [CHECKPOINT 3: pre_llm_request] User prompt length: %d chars (%d pages included).",
        len(user_prompt),
        len(included_pages),
    )

    # 6. Invoke OpenAI Structured Output
    await _emit_progress(
        "writing_overview",
        f"Synthesizing and writing final business overview with {resolved_model}...",
        {"model": resolved_model, "page_count": len(included_pages)},
    )
    try:
        client = AsyncOpenAI(api_key=raw_api_key, timeout=timeout_seconds)

        t_llm = time.perf_counter()
        completion = await client.beta.chat.completions.parse(
            model=resolved_model,
            messages=[
                {"role": "system", "content": BUSINESS_OVERVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format=BusinessOverview,
        )
        llm_duration = round(time.perf_counter() - t_llm, 2)
        usage = getattr(completion, "usage", None)
        prompt_tokens = usage.prompt_tokens if usage else None
        completion_tokens = usage.completion_tokens if usage else None
        total_tokens = usage.total_tokens if usage else None

        logger.info(
            "[BUSINESS_OVERVIEW_LLM] website='%s' model='%s' duration_seconds=%.2f "
            "pages=%d prompt_chars=%d prompt_tokens=%s completion_tokens=%s total_tokens=%s",
            resolved_website_url,
            resolved_model,
            llm_duration,
            len(included_pages),
            len(user_prompt),
            prompt_tokens,
            completion_tokens,
            total_tokens,
        )

        parsed_overview: BusinessOverview | None = completion.choices[0].message.parsed
        if parsed_overview is None:
            raw_content = getattr(completion.choices[0].message, "content", None) or ""
            if raw_content:
                logger.warning("Structured parse returned None; attempting JSON repair on raw message content.")
                try:
                    json_match = re.search(r"\{.*\}", raw_content, re.DOTALL)
                    if json_match:
                        raw_json = json.loads(json_match.group(0))
                        parsed_overview = BusinessOverview.model_validate(raw_json)
                        logger.info("Successfully recovered BusinessOverview via local JSON repair from raw content.")
                except Exception as repair_exc:
                    logger.warning("Local JSON extraction failed: %s", repair_exc)

            enable_repair = getattr(settings, "ENABLE_JSON_REPAIR_RETRY", True)
            if parsed_overview is None and isinstance(enable_repair, bool) and enable_repair and raw_content:
                try:
                    logger.info("Sending single repair prompt to OpenAI to recover valid BusinessOverview JSON.")
                    repair_completion = await client.beta.chat.completions.parse(
                        model=resolved_model,
                        messages=[
                            {"role": "system", "content": "You are a JSON repair assistant. Output ONLY valid JSON adhering strictly to the BusinessOverview schema."},
                            {"role": "user", "content": f"The following JSON response was invalid or malformed. Repair it so it strictly parses as valid BusinessOverview JSON:\n\n{raw_content}"},
                        ],
                        response_format=BusinessOverview,
                    )
                    parsed_overview = repair_completion.choices[0].message.parsed
                    if parsed_overview:
                        logger.info("Successfully recovered BusinessOverview via LLM repair retry.")
                except Exception as llm_repair_exc:
                    logger.warning("LLM repair retry failed: %s", llm_repair_exc)

        if parsed_overview is None:
            err = "OpenAI returned an empty or unparseable structured response."
            logger.error(err)
            return BusinessOverviewResult(
                status="failed",
                business_overview=None,
                website_url=resolved_website_url,
                total_pages_analyzed=len(included_pages),
                total_findings=0,
                model_used=resolved_model,
                execution_time_seconds=round(time.perf_counter() - start_time, 2),
                error_message=err,
            )

        logger.info(
            "[business_overview_trace] [CHECKPOINT 4: raw_llm_response] Received raw LLM response: %d products, %d VPs.",
            len(parsed_overview.products_and_services),
            len(parsed_overview.positioning_and_value_propositions),
        )

        # 7. Validate and sanitize source URLs against pages actually supplied in prompt
        valid_source_urls = {p.url for p in included_pages}
        sanitized_overview = _sanitize_and_validate_overview(
            parsed_overview,
            valid_source_urls=valid_source_urls,
            fallback_website_url=resolved_website_url,
            business_form=business_form,
        )

        exec_time = round(time.perf_counter() - start_time, 2)
        logger.info(
            "Successfully generated business overview for %s in %.2fs (%d products).",
            resolved_website_url,
            exec_time,
            len(sanitized_overview.products_and_services),
        )

        return BusinessOverviewResult(
            status="success",
            business_overview=sanitized_overview,
            website_url=resolved_website_url,
            total_pages_analyzed=len(included_pages),
            total_findings=0,
            model_used=resolved_model,
            execution_time_seconds=exec_time,
            error_message=None,
        )

    except OpenAIError as exc:
        err_msg = f"OpenAI API error during overview generation: {exc}"
        logger.error(err_msg)
        return BusinessOverviewResult(
            status="failed",
            business_overview=None,
            website_url=resolved_website_url,
            total_pages_analyzed=len(usable_pages),
            total_findings=0,
            model_used=resolved_model,
            execution_time_seconds=round(time.perf_counter() - start_time, 2),
            error_message=err_msg,
        )
    except Exception as exc:
        err_msg = f"Unexpected failure in business overview generation: {exc}"
        logger.error(err_msg)
        return BusinessOverviewResult(
            status="failed",
            business_overview=None,
            website_url=resolved_website_url,
            total_pages_analyzed=len(usable_pages),
            total_findings=0,
            model_used=resolved_model,
            execution_time_seconds=round(time.perf_counter() - start_time, 2),
            error_message=err_msg,
        )
