"""Business Overview Generation Service.

Converts successfully crawled website pages into a concise, structured,
and strictly evidence-based business overview for downstream competitor analysis.

Key Responsibilities:
- Accept crawled content from :class:`WebsiteCrawlResult` or a sequence of :class:`ScrapedPage` models.
- Validate that sufficient, usable content exists before invoking any LLM.
- Ground all facts, products, services, features, and positioning in the provided website text.
- Enforce strict accuracy: no hallucinated facts, prices, audience claims, or marketing advice.
- Distinguish between explicit statements and inferred observations.
- Preserve source URLs on every finding and item to ensure full traceability, including multi-page attribution.
- Support accurate offering classification: individual products, variants, collections, services, packages, SaaS features, and subscription plans.
- Support structured pricing: price, currency, original price, discounts, billing period, and availability status.
- Identify and catalog information gaps and explicit discrepancies between user intake and website content.
- Use the shared canonical model configuration (``settings.LLM_MODEL``).
- Bound token usage by applying deterministic water-filling per-page and total input character limits.
- Return a structured :class:`BusinessOverviewResult` leveraging existing schemas (:class:`Finding`).
"""

from __future__ import annotations

import logging
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
from backend.app.schemas.enums import FindingType, OfferingType, PageType, PriceAvailability
from backend.app.schemas.scraped_data import Finding, ScrapedPage
from .website_content_crawler_service import WebsiteCrawlResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Operational & Cost Control Limits
# ---------------------------------------------------------------------------

# Minimum total characters required across all pages to warrant an LLM generation call
MIN_TOTAL_CONTENT_CHARS: int = 150

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
    """Represents a discrete product, service, collection, feature, or plan offered by the business."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(description="Name or title of the product, service, collection, or plan.")
    offering_type: OfferingType = Field(
        default=OfferingType.PRODUCT,
        description="Classification: 'product', 'product_variant', 'collection', 'service', 'service_package', 'saas_feature', 'subscription_plan', 'bundle', or 'other'.",
    )
    description: str = Field(description="Factual description based strictly on the website text.")
    pricing: PricingInfo | None = Field(
        default=None,
        description="Commercial pricing, discount, or subscription terms if available or stated on the website.",
    )
    variants: list[str] = Field(
        default_factory=list,
        description="Explicit product variants such as shades, sizes, colors, or editions if applicable.",
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
    key_findings: list[Finding] = Field(
        default_factory=list,
        description="Granular findings categorized by FindingType, tracking statements, inferences, and source URLs.",
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
# Diagnostic Tracing Helpers
# ---------------------------------------------------------------------------

def _diag_check_craze(text: str | None) -> bool:
    """Diagnostic helper: check if text contains CRAZE product identifier."""
    if not text:
        return False
    lower = text.lower()
    return "craze" in lower or "ph-lease" in lower


def _diag_check_price_269(text: str | None) -> bool:
    """Diagnostic helper: check if text contains price 269 or ₹269."""
    if not text:
        return False
    return "269" in text or "\u20b9269" in text or "₹269" in text


def _diag_check_price_175(text: str | None) -> bool:
    """Diagnostic helper: check if text contains coupon price 175 or ₹175."""
    if not text:
        return False
    return "175" in text or "\u20b9175" in text or "₹175" in text


# ---------------------------------------------------------------------------
# Helper Functions: Page Extraction & Fair Budget Allocation
# ---------------------------------------------------------------------------

def _extract_usable_pages(
    crawl_input: WebsiteCrawlResult | Sequence[ScrapedPage],
) -> tuple[list[ScrapedPage], str]:
    """Extract and validate non-empty ScrapedPage models and infer the base website URL.

    Filters out empty pages with zero readable text content.

    Returns:
        (usable_pages, website_url)
    """
    usable_pages: list[ScrapedPage] = []
    website_url: str = ""

    if isinstance(crawl_input, WebsiteCrawlResult):
        website_url = crawl_input.website_url
        for page in crawl_input.scraped_pages:
            if page.content and page.content.strip():
                usable_pages.append(page)
    elif isinstance(crawl_input, Sequence):
        for item in crawl_input:
            if isinstance(item, ScrapedPage) and item.content and item.content.strip():
                usable_pages.append(item)
                if not website_url and item.url:
                    parsed = urllib.parse.urlsplit(item.url)
                    website_url = f"{parsed.scheme}://{parsed.netloc}"

    return usable_pages, website_url


def _format_pages_for_prompt(
    pages: list[ScrapedPage],
) -> tuple[str, list[ScrapedPage]]:
    """Format complete scraped page content for the LLM prompt.

    Iterates over usable pages with non-empty content and formats each page
    with its URL, title, page type, and complete un-truncated content.

    Returns:
        (formatted_pages_text, included_pages)
    """
    if not pages:
        return "", []

    usable = [p for p in pages if p.content and p.content.strip()]
    if not usable:
        return "", []

    formatted_chunks: list[str] = []
    included_pages: list[ScrapedPage] = []

    for idx, page in enumerate(usable, start=1):
        title_str = f" | Title: {page.title.strip()}" if page.title and page.title.strip() else ""
        type_str = f" | Type: {page.page_type.value}" if hasattr(page.page_type, "value") else ""
        header = f"--- Page {idx}: {page.url}{title_str}{type_str} ---"

        page_text = page.content.strip()
        chunk = f"{header}\n{page_text}\n"
        formatted_chunks.append(chunk)
        included_pages.append(page)

        included_records.append({
            "index": idx,
            "url": page.url,
            "raw_chars": len(page.content.strip()),
            "allocated_chars": allocated,
            "retained_chars": len(chunk),
            "is_truncated": is_truncated,
            "has_craze": _diag_check_craze(chunk),
            "has_269": _diag_check_price_269(chunk),
            "has_175": _diag_check_price_175(chunk),
        })

    final_formatted = "\n".join(formatted_chunks)

    logger.info(
        "[business_overview_trace] [CHECKPOINT 2: format_pages] Formatted prompt: included=%d/%d pages (%d chars, max=%d). has_craze=%s, has_269=%s, has_175=%s",
        len(included_pages),
        len(pages),
        len(final_formatted),
        max_total_chars,
        _diag_check_craze(final_formatted),
        _diag_check_price_269(final_formatted),
        _diag_check_price_175(final_formatted),
    )
    for rec in included_records:
        status_str = "partially_truncated" if rec["is_truncated"] else "fully_retained"
        logger.info(
            "[business_overview_trace] [CHECKPOINT 2: format_pages] Page %d: url=%s, raw=%d, retained=%d (%s)",
            rec["index"],
            rec["url"],
            rec["raw_chars"],
            rec["retained_chars"],
            status_str,
        )

    return final_formatted, included_pages


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
    - For key_findings, since Finding requires a verified URL, remove unsupported findings.
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
    for p in overview.products_and_services:
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

    # 4. Sanitize key findings (remove findings citing un-crawled/unverified URLs)
    verified_findings: list[Finding] = []
    dropped_findings_count = 0
    for finding in overview.key_findings:
        verified_finding_sources: list[str] = []
        if hasattr(finding, "source_urls") and finding.source_urls:
            for s_url in finding.source_urls:
                ver = _resolve_and_verify_source_url(s_url, norm_to_original, path_to_original, resolved_base)
                if ver and ver not in verified_finding_sources:
                    verified_finding_sources.append(ver)

        verified_primary = _resolve_and_verify_source_url(
            finding.source_url, norm_to_original, path_to_original, resolved_base
        )
        if verified_primary and verified_primary not in verified_finding_sources:
            verified_finding_sources.insert(0, verified_primary)

        if verified_finding_sources:
            finding.source_urls = verified_finding_sources
            finding.source_url = verified_finding_sources[0]
            verified_findings.append(finding)
        else:
            dropped_findings_count += 1
            logger.warning(
                "Dropping unsupported finding '%s' citing un-crawled/unverified source: '%s'",
                finding.statement[:60],
                finding.source_url,
            )
    overview.key_findings = verified_findings

    # 5. Compile sources_used strictly from verified sources
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
    for f in overview.key_findings:
        if f.source_url:
            verified_sources.add(f.source_url)
        for u in f.source_urls:
            verified_sources.add(u)
    if overview.target_audience and overview.target_audience.source_url:
        verified_sources.add(overview.target_audience.source_url)

    # Check any explicit sources cited in overview.sources_used
    for cited in overview.sources_used:
        ver = _resolve_and_verify_source_url(cited, norm_to_original, path_to_original, resolved_base)
        if ver:
            verified_sources.add(ver)

    overview.sources_used = sorted(list(verified_sources))

    # 6. Harmonize with user-provided intake data and detect explicit conflicts
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
        "[business_overview_trace] [CHECKPOINT 5: sanitization] Sanitization complete: %d findings verified, %d dropped. %d verified sources in sources_used, %d conflicts detected.",
        len(overview.key_findings),
        dropped_findings_count,
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
    start_time = time.monotonic()

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
            execution_time_seconds=round(time.monotonic() - start_time, 2),
            error_message=msg,
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
            execution_time_seconds=round(time.monotonic() - start_time, 2),
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
            execution_time_seconds=round(time.monotonic() - start_time, 2),
            error_message=err,
        )

    # 5. Build prompt with complete page content and user intake context
    stated_name_desc = ""
    stated_product = ""
    stated_audience = ""
    audience_unknown = False
    stated_social = ""

    if hasattr(business_form, "business_name_and_description"):
        stated_name_desc = business_form.business_name_and_description
        stated_product = business_form.product_or_service
        stated_audience = business_form.target_audience or ""
        audience_unknown = business_form.audience_unknown
        stated_social = business_form.social_link or ""
    elif isinstance(business_form, dict):
        stated_name_desc = business_form.get("business_name_and_description", "")
        stated_product = business_form.get("product_or_service", "")
        stated_audience = business_form.get("target_audience", "") or ""
        audience_unknown = bool(business_form.get("audience_unknown", False))
        stated_social = business_form.get("social_link", "") or ""

    intake_parts: list[str] = []
    if stated_name_desc:
        intake_parts.append(f"- Stated Business Identity: {stated_name_desc}")
    if stated_product:
        intake_parts.append(f"- Primary Product / Service Focus: {stated_product}")
    if stated_audience:
        intake_parts.append(f"- Stated Target Audience: {stated_audience}")
    elif audience_unknown:
        intake_parts.append("- Stated Target Audience: Not specified by user (audience_unknown=True)")
    if stated_social:
        intake_parts.append(f"- Stated Social Link: {stated_social}")

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
    try:
        client = AsyncOpenAI(api_key=raw_api_key, timeout=timeout_seconds)

        completion = await client.beta.chat.completions.parse(
            model=resolved_model,
            messages=[
                {"role": "system", "content": BUSINESS_OVERVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format=BusinessOverview,
        )

        parsed_overview: BusinessOverview | None = completion.choices[0].message.parsed
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
                execution_time_seconds=round(time.monotonic() - start_time, 2),
                error_message=err,
            )

        logger.info(
            "[business_overview_trace] [CHECKPOINT 4: raw_llm_response] Received raw LLM response: %d products, %d findings, %d VPs.",
            len(parsed_overview.products_and_services),
            len(parsed_overview.key_findings),
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

        total_findings = len(sanitized_overview.key_findings)
        exec_time = round(time.monotonic() - start_time, 2)
        logger.info(
            "Successfully generated business overview for %s in %.2fs (%d findings, %d products).",
            resolved_website_url,
            exec_time,
            total_findings,
            len(sanitized_overview.products_and_services),
        )

        return BusinessOverviewResult(
            status="success",
            business_overview=sanitized_overview,
            website_url=resolved_website_url,
            total_pages_analyzed=len(included_pages),
            total_findings=total_findings,
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
            execution_time_seconds=round(time.monotonic() - start_time, 2),
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
            execution_time_seconds=round(time.monotonic() - start_time, 2),
            error_message=err_msg,
        )
