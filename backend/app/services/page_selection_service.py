"""OpenAI Page Selection Service.

Evaluates discovered candidate website URLs and selects a balanced, representative
set of pages for competitive marketing analysis using structured LLM output with a
robust deterministic fallback.

Responsibilities:
- Accept combined candidate URLs from `discover_website_urls()`.
- Use the shared canonical model configuration (`settings.LLM_MODEL`).
- Ensure balanced coverage across key business categories when available:
  1. Homepage (identity, baseline positioning, core value proposition)
  2. About / Company (background, credentials, brand story)
  3. Products / Services (features, offerings, prioritizing target product if specified)
  4. Pricing / Offers (plans, rates, discounts, promotional offers)
  5. Contact / Booking (conversion, appointments, service access)
  6. Supporting Context (FAQs, customer policies, case studies)
- Adapt selection to different business models (e-commerce, service, SaaS, static).
- Enforce strict validation: every selected URL must exist in the candidate list,
  deduplicate selections, avoid variant crowding, and never fabricate URLs.
- Provide a deterministic priority-budgeted fallback if LLM is unavailable,
  unconfigured, fails, or returns incomplete/unbalanced results.
- Expose a clean pipeline interface `discover_and_select_pages()` connecting
  discovery to selection for downstream scraping.
"""

from __future__ import annotations

from enum import Enum
import logging
import re
from typing import Any, Literal
import urllib.parse

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from backend.app.core.config import settings
from backend.app.agent.prompts.page_selection import (
    PAGE_SELECTION_SYSTEM_PROMPT,
    PAGE_SELECTION_USER_PROMPT_TEMPLATE,
)
from backend.app.schemas.enums import PageType
from .sitemap_fallback_service import extract_product_keywords
from .sitemap_fallback_service import WebsiteURLDiscoveryResult, discover_website_urls
from .url_extractor_service import CandidateURL

logger = logging.getLogger(__name__)

# Canonical model identifier for structured page selection (configured via settings.LLM_MODEL)
PAGE_SELECTION_MODEL: str = settings.LLM_MODEL

# Default maximum number of pages to select for competitive analysis
DEFAULT_MAX_SELECTED_PAGES: int = 10

# Upper bound on candidate URLs sent to LLM prompt to keep latency and token costs minimal
MAX_PROMPT_CANDIDATES: int = 60


# ---------------------------------------------------------------------------
# Balanced Selection Categories & Priorities
# ---------------------------------------------------------------------------

class SelectionCategory(str, Enum):
    """Broad selection categories for balanced competitive analysis."""

    HOME = "home"
    ABOUT = "about"
    PRODUCT_SERVICE = "product_service"
    PRICING_OFFER = "pricing_offer"
    CONTACT_BOOKING = "contact_booking"
    OTHER_RELEVANT = "other_relevant"


PAGE_TYPE_PRIORITY: dict[PageType, int] = {
    PageType.PRICING: 100,
    PageType.PRODUCT: 90,
    PageType.SERVICE: 90,
    PageType.OFFER: 85,
    PageType.ABOUT: 80,
    PageType.HOME: 75,
    PageType.CONTACT: 70,
    PageType.FAQ: 65,
    PageType.BLOG: 35,
    PageType.OTHER: 30,
    PageType.POLICY: 25,
    PageType.UNKNOWN: 15,
}

SECTION_PRIORITY_BONUS: dict[str, int] = {
    "nav": 15,
    "header": 15,
    "footer": 5,
    "content": 0,
    "sitemap": 0,
}


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class PageSelectionDecision(BaseModel):
    """LLM decision for a single selected candidate URL."""

    url: str = Field(description="Exact URL chosen from the provided candidate list.")
    reason: str = Field(
        description="Concise rationale explaining why this page is valuable for competitor marketing intelligence."
    )


class StructuredPageSelectionResponse(BaseModel):
    """Structured response format returned by the OpenAI model."""

    selected_pages: list[PageSelectionDecision] = Field(
        default_factory=list,
        description="Selected candidate page URLs prioritized for competitor marketing analysis.",
    )


class SelectedPage(BaseModel):
    """A final selected candidate URL with associated discovery metadata and selection rationale."""

    url: str = Field(description="Selected canonical URL.")
    page_type: PageType = Field(default=PageType.UNKNOWN, description="Classified page type category.")
    link_text: str = Field(default="", description="Visible anchor text or accessible label if available.")
    source_section: str = Field(
        default="unknown",
        description="HTML section or mechanism where link was discovered (header, nav, footer, content, sitemap).",
    )
    selection_reason: str = Field(
        default="",
        description="Rationale for selecting this page (from LLM decision or deterministic priority scoring).",
    )


class PageSelectionResult(BaseModel):
    """Structured result returned by the page selection service."""

    selected_pages: list[SelectedPage] = Field(
        default_factory=list,
        description="Final selected candidate pages for downstream crawling.",
    )
    total_selected: int = Field(default=0, description="Number of pages selected.")
    selection_method: Literal["llm", "deterministic_fallback", "empty"] = Field(
        description="Method used for page selection ('llm', 'deterministic_fallback', or 'empty')."
    )
    model_used: str | None = Field(
        default=None,
        description="OpenAI model identifier used if LLM selection succeeded.",
    )
    error_message: str | None = Field(
        default=None,
        description="Diagnostic or error message if LLM selection failed or fallback was used.",
    )


class WebsitePipelineResult(BaseModel):
    """End-to-end result combining URL discovery and page selection for downstream scraping."""

    website_url: str = Field(description="Initial requested website URL.")
    base_url: str = Field(description="Resolved canonical base website URL.")
    status: Literal["success", "partial", "failed"] = Field(
        description="Overall pipeline status ('success', 'partial', or 'failed')."
    )
    discovery_result: WebsiteURLDiscoveryResult = Field(
        description="Detailed metadata and candidates discovered from homepage and sitemap fallback."
    )
    selection_result: PageSelectionResult = Field(
        description="Selected pages and selection metadata."
    )
    final_urls: list[str] = Field(
        default_factory=list,
        description="Clean list of selected URL strings ready for the scraping crawler.",
    )
    total_selected: int = Field(default=0, description="Total count of final selected URLs.")


# ---------------------------------------------------------------------------
# Category Classification & Relevance Helpers
# ---------------------------------------------------------------------------

def categorize_candidate(candidate: CandidateURL, base_url: str = "") -> SelectionCategory:
    """Classify a candidate URL into one of six balanced selection categories.

    Uses PageType classification, URL path tokens, and anchor text clues.
    """
    norm_url = candidate.url.lower().rstrip("/")
    clean_base = base_url.lower().rstrip("/") if base_url else ""
    parsed = urllib.parse.urlsplit(norm_url)
    path = parsed.path.strip("/").lower()
    text = (candidate.link_text or "").lower()

    # 1. Homepage
    if (
        candidate.page_type == PageType.HOME
        or (clean_base and norm_url == clean_base)
        or path in ("", "index.html", "index.php", "home")
        or text in ("home", "homepage", "main")
    ):
        return SelectionCategory.HOME

    # 2. About / Company
    if candidate.page_type == PageType.ABOUT:
        return SelectionCategory.ABOUT
    about_tokens = ("about", "who-we-are", "our-story", "our-team", "company", "mission", "leadership", "founder", "founders", "heritage", "brand-story")
    if any(t in path for t in about_tokens) or any(t in text for t in ("about", "who we are", "our story", "our team", "leadership", "company", "mission", "founder", "heritage", "brand story")):
        return SelectionCategory.ABOUT

    # 3. Pricing & Offers
    if candidate.page_type in (PageType.PRICING, PageType.OFFER):
        return SelectionCategory.PRICING_OFFER
    pricing_tokens = (
        "pricing", "prices", "price", "plans", "packages", "rates",
        "subscription", "subscriptions", "fees", "cost", "tier", "tiers",
        "offer", "offers", "deals", "deal", "discount", "discounts",
        "promo", "promotions", "sale", "clearance", "coupons", "coupon",
        "bundle", "bundles", "membership", "memberships", "voucher", "combo",
    )
    path_tokens = set(re.split(r"[-_/]", path))
    if any(t in path_tokens for t in pricing_tokens) or any(t in text for t in ("pricing", "plans", "rates", "subscription", "packages", "deals", "offers", "sale", "discount", "bundle", "bundles", "coupons", "membership")):
        return SelectionCategory.PRICING_OFFER

    # 4. Contact & Booking
    if candidate.page_type == PageType.CONTACT:
        return SelectionCategory.CONTACT_BOOKING
    contact_tokens = (
        "contact", "book", "booking", "appointment", "schedule", "quote",
        "demo", "request-demo", "locations", "location", "reach", "offices",
    )
    if any(t in path_tokens for t in contact_tokens) or any(t in text for t in ("contact", "get in touch", "book", "booking", "appointment", "schedule", "quote", "demo", "request demo")):
        return SelectionCategory.CONTACT_BOOKING

    # 5. Products / Services
    if candidate.page_type in (PageType.PRODUCT, PageType.SERVICE):
        return SelectionCategory.PRODUCT_SERVICE
    product_service_tokens = (
        "product", "products", "shop", "store", "collection", "collections",
        "category", "categories", "catalog", "catalogue", "merchandise", "items", "goods", "features", "feature", "platform",
        "service", "services", "solution", "solutions", "offerings", "capabilities",
        "treatments", "consulting", "use-case", "use-cases", "case-study", "case-studies", "portfolio",
    )
    if any(t in path_tokens for t in product_service_tokens) or any(t in text for t in ("product", "products", "shop", "collection", "category", "service", "services", "solutions", "features", "portfolio")):
        return SelectionCategory.PRODUCT_SERVICE

    # 6. Other Relevant (FAQ, Policies, Blog, Reviews)
    return SelectionCategory.OTHER_RELEVANT


def calculate_product_relevance(
    candidate: CandidateURL,
    keywords: list[str],
    full_phrase: str = "",
) -> int:
    """Calculate a relevance score for a candidate URL against the user's product/service.

    Gives strong priority to exact phrase matches and token matches in URL path or link text,
    plus collection/category page boosts for relevant product areas.
    """
    if not keywords and not full_phrase:
        return 0

    norm_url = candidate.url.lower()
    norm_text = (candidate.link_text or "").lower()
    score = 0

    # 1. Exact phrase match (e.g. "lip gloss" or "lip-gloss")
    if full_phrase:
        clean_phrase = full_phrase.lower().strip()
        slug_phrase = re.sub(r"\s+", "-", clean_phrase)
        if clean_phrase in norm_text or slug_phrase in norm_url or clean_phrase in norm_url:
            score += 150

    # 2. Token matches
    matching_tokens = sum(1 for kw in keywords if kw in norm_url or kw in norm_text)
    if matching_tokens > 0:
        score += matching_tokens * 35
        if matching_tokens == len(keywords) and len(keywords) > 1:
            score += 60

    # 3. Collection / category boost when matching
    if score > 0:
        if any(cat in norm_url for cat in ("/collection", "/collections", "/category", "/categories")):
            score += 50
        elif candidate.page_type in (PageType.PRODUCT, PageType.SERVICE):
            score += 30

    return score


def _get_path_stem(url: str) -> str:
    """Extract a normalized path stem without trailing query params or variant tokens."""
    parsed = urllib.parse.urlsplit(url.lower().rstrip("/"))
    path = parsed.path
    # Strip common product variant suffixes (e.g. -shade-01, -01, -shade-pink, etc.)
    stem = re.sub(r"[-_](?:shade|color|colour|size|variant|v)?[0-9]+$", "", path)
    stem = re.sub(r"[-_](?:shade|color|colour|size|variant)[-_][a-z0-9]+$", "", stem)
    return stem


def _count_stem_occurrences(url: str, selected_urls: set[str]) -> int:
    """Count how many already selected URLs share the same normalized path stem."""
    stem = _get_path_stem(url)
    return sum(1 for sel in selected_urls if _get_path_stem(sel) == stem)


# ---------------------------------------------------------------------------
# Deterministic Balanced Page Selection Implementation
# ---------------------------------------------------------------------------

def select_pages_deterministic(
    candidates: list[CandidateURL],
    *,
    max_pages: int = DEFAULT_MAX_SELECTED_PAGES,
    base_url: str = "",
    product_or_service: str | None = None,
) -> PageSelectionResult:
    """Deterministically select a balanced, representative set of up to `max_pages` candidate URLs.

    Balanced selection strategy:
    - Normalizes and deduplicates discovered candidates.
    - Groups candidates into 6 core business dimensions:
      1. Homepage (identity, baseline positioning)
      2. About / Company (background, credentials)
      3. Products / Services (features, offerings, prioritizing target product if specified)
      4. Pricing / Offers (pricing plans, rates, promotional offers)
      5. Contact / Booking (conversion, service access)
      6. Supporting context (FAQs, key policies, case studies)
    - Allocates slots across available categories so that no single category (such as
      many similar product variants) crowds out essential business context.
    - If a category is absent on the website, its slot automatically flows to the next
      most informative available pages.
    - Never invents URLs; every selected page originates from `candidates`.

    Args:
        candidates: Discovered candidate URLs.
        max_pages: Maximum number of pages to select.
        base_url: Website base URL to identify the homepage.
        product_or_service: Target product or service from user business intake.

    Returns:
        A populated :class:`PageSelectionResult` instance.
    """
    if not candidates:
        return PageSelectionResult(
            selected_pages=[],
            total_selected=0,
            selection_method="empty",
            error_message="Candidate URL list is empty; no pages to select.",
        )

    # 1. Deduplicate candidates by normalized URL preserving first occurrence
    seen_normalized: set[str] = set()
    unique_candidates: list[CandidateURL] = []
    for c in candidates:
        norm_key = c.url.lower().rstrip("/")
        if norm_key not in seen_normalized:
            seen_normalized.add(norm_key)
            unique_candidates.append(c)

    # 2. Extract product relevance parameters
    keywords = extract_product_keywords(product_or_service) if product_or_service else []
    product_phrase = product_or_service.strip() if product_or_service else ""
    has_matches = any(calculate_product_relevance(c, keywords, product_phrase) > 0 for c in unique_candidates)

    # 3. Categorize all unique candidates
    buckets: dict[SelectionCategory, list[CandidateURL]] = {cat: [] for cat in SelectionCategory}
    for c in unique_candidates:
        cat = categorize_candidate(c, base_url=base_url)
        buckets[cat].append(c)

    # 4. Helper to score and rank candidates within their category
    def _rank_candidate(c: CandidateURL) -> tuple[int, int, str]:
        type_score = PAGE_TYPE_PRIORITY.get(c.page_type, 15)
        section_score = SECTION_PRIORITY_BONUS.get(c.source_section, 0)
        relevance = calculate_product_relevance(c, keywords, product_phrase) if keywords else 0
        total_score = type_score + section_score + relevance

        # Collection / category boost
        norm_url = c.url.lower()
        if any(seg in norm_url for seg in ("/collection", "/collections", "/category", "/categories")):
            total_score += 40

        # Sub-type bonuses within category
        if c.page_type == PageType.FAQ:
            total_score += 10
        elif c.page_type == PageType.POLICY:
            if any(term in norm_url for term in ("shipping", "refund", "return", "delivery")):
                total_score += 10
            elif any(term in norm_url for term in ("privacy", "terms", "cookie")):
                total_score -= 15

        # When matching product pages exist, penalize unrelated product pages
        if has_matches and relevance == 0 and c.page_type == PageType.PRODUCT:
            total_score -= 60

        path_segments = len([s for s in urllib.parse.urlsplit(c.url).path.split("/") if s])
        return (total_score, -path_segments, c.url)

    # Sort each category bucket by ranking score
    for cat in buckets:
        buckets[cat].sort(key=_rank_candidate, reverse=True)

    # 5. Execute Multi-Stage Priority Budgeting
    selected_pages: list[SelectedPage] = []
    selected_urls: set[str] = set()

    def _add_page(candidate: CandidateURL, reason: str) -> bool:
        if len(selected_pages) >= max_pages:
            return False
        norm_key = candidate.url.lower().rstrip("/")
        if norm_key in selected_urls:
            return False
        selected_urls.add(norm_key)
        selected_pages.append(
            SelectedPage(
                url=candidate.url,
                page_type=candidate.page_type,
                link_text=candidate.link_text,
                source_section=candidate.source_section,
                selection_reason=reason,
            )
        )
        return True

    # --- Phase 1: Homepage (Identity & baseline positioning) ---
    if buckets[SelectionCategory.HOME] and len(selected_pages) < max_pages:
        home_c = buckets[SelectionCategory.HOME][0]
        _add_page(
            home_c,
            "Homepage provides baseline brand identity, positioning, and overall navigation context.",
        )

    # --- Phase 2: About / Company Story (Foundational background & credibility) ---
    if buckets[SelectionCategory.ABOUT] and len(selected_pages) < max_pages:
        about_c = buckets[SelectionCategory.ABOUT][0]
        _add_page(
            about_c,
            "About page provides foundational brand background, company story, and positioning context.",
        )

    # --- Phase 3: Target Product / Service Offerings ---
    # When target product/service matches exist, prioritize matching collection + product pages.
    # Otherwise select top general product/service offerings.
    prod_candidates = buckets[SelectionCategory.PRODUCT_SERVICE]
    if prod_candidates and len(selected_pages) < max_pages:
        # Determine quota for product/service in this first pass (leave room for pricing/contact/etc.)
        pricing_candidates = buckets[SelectionCategory.PRICING_OFFER]
        slots_remaining = max_pages - len(selected_pages)
        if has_matches:
            # Allocate up to min(3, remaining slots) for relevant products
            prod_quota = min(3, slots_remaining)
            matching_prod = [c for c in prod_candidates if calculate_product_relevance(c, keywords, product_phrase) > 0]
            unrelated_prod = [c for c in prod_candidates if calculate_product_relevance(c, keywords, product_phrase) == 0]
            prod_queue = matching_prod + unrelated_prod
        else:
            prod_quota = min(2, slots_remaining)
            prod_queue = prod_candidates

        # If pricing/offer candidates exist and slots are tight, reserve at least 1 slot for them
        if pricing_candidates and slots_remaining <= 3 and prod_quota >= slots_remaining:
            prod_quota = max(1, slots_remaining - 1)

        added_prod = 0
        # Check if diverse product stems exist
        prod_stems = {_get_path_stem(c.url) for c in prod_queue}
        has_diverse_stems = len(prod_stems) > 1

        for pc in prod_queue:
            if added_prod >= prod_quota or len(selected_pages) >= max_pages:
                break
            # If diverse stems exist, don't pick multiple variants of the same item in first pass
            stem_count = _count_stem_occurrences(pc.url, selected_urls)
            if has_diverse_stems and stem_count >= 1:
                continue

            rel = calculate_product_relevance(pc, keywords, product_phrase) if keywords else 0
            type_name = pc.page_type.value if hasattr(pc.page_type, "value") else str(pc.page_type)
            if rel > 0:
                reason = f"Selected for target product relevance ({product_phrase}) from {pc.source_section}."
            elif product_phrase and not has_matches:
                reason = f"Priority {type_name} page selected via deterministic hierarchy (no matching pages for '{product_phrase}' found)."
            else:
                reason = f"Priority {type_name} page selected via deterministic hierarchy from {pc.source_section}."

            if _add_page(pc, reason):
                added_prod += 1

    # --- Phase 4: Pricing & Offers ---
    pricing_candidates = buckets[SelectionCategory.PRICING_OFFER]
    if pricing_candidates and len(selected_pages) < max_pages:
        # Select up to 2 pricing/offer pages (e.g. 1 pricing + 1 promotion/offer)
        for pr_c in pricing_candidates[:2]:
            if len(selected_pages) >= max_pages:
                break
            type_name = pr_c.page_type.value if hasattr(pr_c.page_type, "value") else str(pr_c.page_type)
            if pr_c.page_type == PageType.PRICING:
                reason = f"Commercial pricing page providing pricing structure and subscription/rate details from {pr_c.source_section}."
            else:
                reason = f"Promotional offers/deals page providing commercial discount and incentive intelligence from {pr_c.source_section}."
            _add_page(pr_c, reason)

    # --- Phase 5: Contact / Booking ---
    contact_candidates = buckets[SelectionCategory.CONTACT_BOOKING]
    if contact_candidates and len(selected_pages) < max_pages:
        con_c = contact_candidates[0]
        _add_page(
            con_c,
            f"Contact and service access page providing booking, appointment, or customer inquiry pathways from {con_c.source_section}.",
        )

    # --- Phase 6: Supporting Information (FAQ, Shipping/Returns, Case Studies) ---
    other_candidates = buckets[SelectionCategory.OTHER_RELEVANT]
    if other_candidates and len(selected_pages) < max_pages:
        for oth_c in other_candidates:
            if len(selected_pages) >= max_pages:
                break
            type_name = oth_c.page_type.value if hasattr(oth_c.page_type, "value") else str(oth_c.page_type)
            if oth_c.page_type == PageType.FAQ:
                reason = f"FAQ support page providing customer questions, terms, and service answers from {oth_c.source_section}."
            elif oth_c.page_type == PageType.POLICY:
                reason = f"Customer policy page (shipping/returns/terms) providing fulfillment terms from {oth_c.source_section}."
            else:
                reason = f"Supporting context page ({type_name}) providing business intelligence from {oth_c.source_section}."
            if _add_page(oth_c, reason):
                break

    # --- Phase 7: Fill Remaining Slots with Best Available Candidates ---
    # If any slots remain (e.g. site has no pricing page, no contact page, or has more capacity),
    # backfill with the highest-scoring remaining candidates across all categories.
    if len(selected_pages) < max_pages:
        remaining_candidates = [
            c for c in unique_candidates
            if c.url.lower().rstrip("/") not in selected_urls
        ]
        # Sort remaining by rank
        remaining_candidates.sort(key=_rank_candidate, reverse=True)

        # First try candidates with fresh stems that have not been selected yet
        fresh_stem_candidates = [c for c in remaining_candidates if _count_stem_occurrences(c.url, selected_urls) == 0]
        duplicate_stem_candidates = [c for c in remaining_candidates if _count_stem_occurrences(c.url, selected_urls) > 0]
        # Prioritize fresh stems, but allow duplicate stems if slots still remain
        ordered_backfill = fresh_stem_candidates + duplicate_stem_candidates

        for rem_c in ordered_backfill:
            if len(selected_pages) >= max_pages:
                break

            rel = calculate_product_relevance(rem_c, keywords, product_phrase) if keywords else 0
            type_name = rem_c.page_type.value if hasattr(rem_c.page_type, "value") else str(rem_c.page_type)
            if rel > 0:
                reason = f"Selected for target product relevance ({product_phrase}) from {rem_c.source_section}."
            elif product_phrase and not has_matches:
                reason = f"Priority {type_name} page selected via deterministic hierarchy (no matching pages for '{product_phrase}' found)."
            else:
                reason = f"Priority {type_name} page selected via deterministic hierarchy from {rem_c.source_section}."

            _add_page(rem_c, reason)

    count = len(selected_pages)
    logger.info("Deterministic selection selected %d page(s) (cap: %d).", count, max_pages)

    return PageSelectionResult(
        selected_pages=selected_pages,
        total_selected=count,
        selection_method="deterministic_fallback",
        model_used=None,
        error_message=None,
    )


# ---------------------------------------------------------------------------
# LLM-Based Page Selection Implementation
# ---------------------------------------------------------------------------

def _select_balanced_candidates_for_prompt(
    candidates: list[CandidateURL],
    website_url: str,
    product_or_service: str | None = None,
    max_prompt_candidates: int = MAX_PROMPT_CANDIDATES,
) -> list[CandidateURL]:
    """Assemble a balanced, representative candidate list for the LLM prompt.

    Ensures that high-volume categories (such as dozens of product variants)
    do not crowd out essential categories (Homepage, About, Pricing, Contact, FAQs)
    from the candidate list sent to OpenAI.
    """
    if len(candidates) <= max_prompt_candidates:
        return candidates

    clean_base = website_url.lower().rstrip("/") if website_url else ""
    keywords = extract_product_keywords(product_or_service) if product_or_service else []
    product_phrase = product_or_service.strip() if product_or_service else ""

    # Group by selection category
    buckets: dict[SelectionCategory, list[CandidateURL]] = {cat: [] for cat in SelectionCategory}
    for c in candidates:
        cat = categorize_candidate(c, base_url=clean_base)
        buckets[cat].append(c)

    def _sort_key(c: CandidateURL) -> tuple[int, int, str]:
        type_score = PAGE_TYPE_PRIORITY.get(c.page_type, 15)
        section_score = SECTION_PRIORITY_BONUS.get(c.source_section, 0)
        relevance = calculate_product_relevance(c, keywords, product_phrase) if keywords else 0
        total_score = type_score + section_score + relevance
        path_segments = len([s for s in urllib.parse.urlsplit(c.url).path.split("/") if s])
        return (total_score, -path_segments, c.url)

    for cat in buckets:
        buckets[cat].sort(key=_sort_key, reverse=True)

    selected_prompt_candidates: list[CandidateURL] = []
    seen: set[str] = set()

    def _add(c: CandidateURL) -> None:
        key = c.url.lower().rstrip("/")
        if key not in seen and len(selected_prompt_candidates) < max_prompt_candidates:
            seen.add(key)
            selected_prompt_candidates.append(c)

    # 1. Guaranteed Core Categories (if available)
    for c in buckets[SelectionCategory.HOME][:2]:
        _add(c)
    for c in buckets[SelectionCategory.ABOUT][:4]:
        _add(c)
    for c in buckets[SelectionCategory.PRICING_OFFER][:6]:
        _add(c)
    for c in buckets[SelectionCategory.CONTACT_BOOKING][:4]:
        _add(c)
    for c in buckets[SelectionCategory.OTHER_RELEVANT][:6]:
        _add(c)

    # 2. Product / Service Offerings (prioritizing target product)
    for c in buckets[SelectionCategory.PRODUCT_SERVICE][:25]:
        _add(c)

    # 3. Fill Remaining Prompt Budget from Highest-Scoring Candidates
    remaining = [c for c in candidates if c.url.lower().rstrip("/") not in seen]
    remaining.sort(key=_sort_key, reverse=True)
    for c in remaining:
        _add(c)
        if len(selected_prompt_candidates) >= max_prompt_candidates:
            break

    return selected_prompt_candidates


def is_selection_ambiguous(
    candidates: list[CandidateURL],
    *,
    max_pages: int = DEFAULT_MAX_SELECTED_PAGES,
    base_url: str = "",
    product_or_service: str | None = None,
) -> tuple[bool, str]:
    """Evaluate whether candidate URL prioritization is genuinely ambiguous.

    Deterministic rules can resolve candidate selection confidently when:
    - Candidate count is within or equal to `max_pages`.
    - Key categories are clearly identified and distinctively prioritized.
    - User's supplied product matches explicit URL/anchor tokens.
    - No tie exists at the cutoff boundary between candidates.

    Returns:
        (is_ambiguous: bool, reason: str)
    """
    if not candidates:
        return False, "no_candidates"

    # Deduplicate candidates by normalized URL
    seen_normalized: set[str] = set()
    unique_candidates: list[CandidateURL] = []
    for c in candidates:
        norm_key = c.url.lower().rstrip("/")
        if norm_key not in seen_normalized:
            seen_normalized.add(norm_key)
            unique_candidates.append(c)

    if len(unique_candidates) <= max_pages:
        return False, "candidate_count_within_budget"

    # Check if OpenAI API key is configured
    if not settings.OPENAI_API_KEY:
        return False, "llm_not_configured"

    raw_key = settings.OPENAI_API_KEY.get_secret_value().strip()
    if not raw_key or raw_key in ("your_openai_api_key_here", ""):
        return False, "llm_placeholder_key"

    # Extract product keywords
    keywords = extract_product_keywords(product_or_service) if product_or_service else []
    product_phrase = product_or_service.strip() if product_or_service else ""
    has_matches = any(calculate_product_relevance(c, keywords, product_phrase) > 0 for c in unique_candidates) if keywords else False

    # Ambiguity Trigger 1: User requested a specific product/service, but no keyword match was found
    # yet there are multiple product/service/other candidates that could be semantically relevant
    if product_phrase and not has_matches:
        prod_candidates = [
            c for c in unique_candidates
            if c.page_type in (PageType.PRODUCT, PageType.SERVICE, PageType.OTHER, PageType.UNKNOWN)
        ]
        if len(prod_candidates) > 2:
            return True, f"product_unmatched_by_keywords ('{product_phrase}')"

    # Score and rank candidates using deterministic rank function
    def _rank_candidate(c: CandidateURL) -> tuple[int, int, str]:
        type_score = PAGE_TYPE_PRIORITY.get(c.page_type, 15)
        section_score = SECTION_PRIORITY_BONUS.get(c.source_section, 0)
        relevance = calculate_product_relevance(c, keywords, product_phrase) if keywords else 0
        total_score = type_score + section_score + relevance

        norm_url = c.url.lower()
        if any(seg in norm_url for seg in ("/collection", "/collections", "/category", "/categories")):
            total_score += 40

        if c.page_type == PageType.FAQ:
            total_score += 10
        elif c.page_type == PageType.POLICY:
            if any(term in norm_url for term in ("shipping", "refund", "return", "delivery")):
                total_score += 10
            elif any(term in norm_url for term in ("privacy", "terms", "cookie")):
                total_score -= 15

        if has_matches and relevance == 0 and c.page_type == PageType.PRODUCT:
            total_score -= 60

        path_segments = len([s for s in urllib.parse.urlsplit(c.url).path.split("/") if s])
        return (total_score, -path_segments, c.url)

    ranked = sorted(unique_candidates, key=_rank_candidate, reverse=True)

    # Ambiguity Trigger 2: High proportion of UNKNOWN or OTHER among top candidates
    top_candidates = ranked[: max_pages * 2]
    unclassified_count = sum(1 for c in top_candidates if c.page_type in (PageType.UNKNOWN, PageType.OTHER))
    if len(top_candidates) >= 4 and (unclassified_count / len(top_candidates)) >= 0.40:
        return True, "high_ratio_of_unclassified_candidates"

    # Ambiguity Trigger 3: Score tie at the cutoff boundary
    if len(ranked) > max_pages:
        score_in = _rank_candidate(ranked[max_pages - 1])[0]
        score_out = _rank_candidate(ranked[max_pages])[0]
        if score_in == score_out and score_in > 0:
            return True, f"score_tie_at_boundary (score: {score_in})"

    return False, "deterministic_selection_confident"


async def select_pages_with_llm(
    candidates: list[CandidateURL],
    website_url: str,
    *,
    product_or_service: str | None = None,
    max_pages: int = DEFAULT_MAX_SELECTED_PAGES,
    model: str | None = None,
    force_llm: bool = False,
) -> PageSelectionResult:
    """Select a balanced set of up to `max_pages` relevant candidate URLs.

    Deterministic, rule-based prioritization is the primary selection method.
    The LLM is invoked only as a bounded fallback when deterministic rules cannot
    confidently resolve genuinely ambiguous candidates and an LLM call is justified.

    Validates that:
    - Every selected URL exists in the input candidate list (rejects hallucinated URLs).
    - Duplicates and near-duplicate variants are pruned.
    - Selections do not exceed `max_pages`.
    - Baseline context (Homepage, About) is preserved if available among candidates.
    - If the LLM returns fewer than `max_pages` pages, remaining slots are backfilled
      using balanced deterministic candidates.
    - If the LLM response is malformed, unavailable, or the API call fails,
      transparently falls back to :func:`select_pages_deterministic`.

    Args:
        candidates: Combined candidate URLs from URL discovery.
        website_url: Target business website URL.
        product_or_service: Optional primary product or service focus.
        max_pages: Maximum number of pages to select.
        model: Optional LLM model identifier. Defaults to shared configuration (settings.LLM_MODEL).
        force_llm: Force the LLM fallback path even when deterministic rules are confident.

    Returns:
        A populated :class:`PageSelectionResult` instance.
    """
    if not candidates:
        return PageSelectionResult(
            selected_pages=[],
            total_selected=0,
            selection_method="empty",
            error_message="Candidate list is empty; no pages to select.",
        )

    # 1. Primary Selection: Run deterministic prioritization first
    deterministic_result = select_pages_deterministic(
        candidates,
        max_pages=max_pages,
        base_url=website_url,
        product_or_service=product_or_service,
    )

    # 2. Check if selection is ambiguous and an LLM fallback call is justified
    is_ambiguous, ambiguity_reason = is_selection_ambiguous(
        candidates,
        max_pages=max_pages,
        base_url=website_url,
        product_or_service=product_or_service,
    )

    if not force_llm and not is_ambiguous:
        logger.info(
            "Deterministic page selection resolved %d pages confidently (%s); skipping LLM call.",
            deterministic_result.total_selected,
            ambiguity_reason,
        )
        return deterministic_result

    # 3. Validate OpenAI API Key for LLM fallback
    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY is not configured; using deterministic fallback.")
        deterministic_result.error_message = "OPENAI_API_KEY is not configured."
        return deterministic_result

    raw_api_key = settings.OPENAI_API_KEY.get_secret_value().strip()
    if not raw_api_key or raw_api_key in ("your_openai_api_key_here", ""):
        logger.warning("OPENAI_API_KEY is set to a placeholder; using deterministic fallback.")
        deterministic_result.error_message = "OPENAI_API_KEY is set to a placeholder value."
        return deterministic_result

    logger.info(
        "Candidate selection is ambiguous (%s); invoking bounded LLM fallback for %s.",
        ambiguity_reason,
        website_url,
    )

    # Resolve LLM model identifier (defaults to shared settings.LLM_MODEL)
    selected_model = model or settings.LLM_MODEL

    # 2. Build candidate lookups for fast, strict validation
    candidate_map: dict[str, CandidateURL] = {c.url: c for c in candidates}
    norm_candidate_map: dict[str, CandidateURL] = {c.url.lower().rstrip("/"): c for c in candidates}

    # 3. Assemble balanced candidate URLs for prompt
    sorted_for_prompt = _select_balanced_candidates_for_prompt(
        candidates,
        website_url=website_url,
        product_or_service=product_or_service,
        max_prompt_candidates=MAX_PROMPT_CANDIDATES,
    )

    formatted_lines: list[str] = []
    for c in sorted_for_prompt:
        text_snippet = f' | Anchor: "{c.link_text}"' if c.link_text else ""
        type_str = c.page_type.value if hasattr(c.page_type, "value") else str(c.page_type)
        formatted_lines.append(f"- URL: {c.url} | Category: {type_str} | Found in: {c.source_section}{text_snippet}")

    product_phrase = product_or_service.strip() if product_or_service else ""
    product_context = f"Target Product / Service: {product_phrase}\n" if product_phrase else ""
    system_prompt = PAGE_SELECTION_SYSTEM_PROMPT.format(max_pages=max_pages, homepage_url=website_url)
    user_prompt = PAGE_SELECTION_USER_PROMPT_TEMPLATE.format(
        website_url=website_url,
        product_context=product_context,
        candidate_count=len(formatted_lines),
        total_candidates=len(candidates),
        candidate_lines="\n".join(formatted_lines),
        max_pages=max_pages,
    )

    # 4. Invoke OpenAI structured output
    try:
        client = AsyncOpenAI(api_key=raw_api_key, timeout=30.0)
        logger.info(
            "Requesting page selection from OpenAI model '%s' for %s (%d candidates).",
            selected_model,
            website_url,
            len(candidates),
        )

        completion = await client.beta.chat.completions.parse(
            model=selected_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=StructuredPageSelectionResponse,
        )

        parsed_response: StructuredPageSelectionResponse | None = completion.choices[0].message.parsed
        if parsed_response is None or not parsed_response.selected_pages:
            logger.warning("OpenAI model returned empty structured selection; using deterministic fallback.")
            res = select_pages_deterministic(candidates, max_pages=max_pages, base_url=website_url, product_or_service=product_or_service)
            res.error_message = "OpenAI returned an empty selection response."
            return res

        # 5. Strict membership, deduplication, and max_pages validation
        validated_pages: list[SelectedPage] = []
        seen_urls: set[str] = set()

        for decision in parsed_response.selected_pages:
            if len(validated_pages) >= max_pages:
                break

            target_url = decision.url.strip()
            norm_target = target_url.lower().rstrip("/")

            if norm_target in seen_urls:
                continue

            # Strict membership verification against candidate list
            matched_candidate = candidate_map.get(target_url) or norm_candidate_map.get(norm_target)
            if matched_candidate is None:
                logger.warning(
                    "Discarding hallucinated or modified URL not in candidate list: '%s'",
                    target_url,
                )
                continue

            seen_urls.add(norm_target)
            validated_pages.append(
                SelectedPage(
                    url=matched_candidate.url,
                    page_type=matched_candidate.page_type,
                    link_text=matched_candidate.link_text,
                    source_section=matched_candidate.source_section,
                    selection_reason=decision.reason.strip() or "Selected by LLM for competitive relevance.",
                )
            )

        if not validated_pages:
            logger.warning("No LLM-selected URLs passed candidate membership validation; using fallback.")
            res = select_pages_deterministic(candidates, max_pages=max_pages, base_url=website_url, product_or_service=product_or_service)
            res.error_message = "All LLM-selected URLs failed candidate membership validation."
            return res

        # 6. Post-LLM Balancing & Slot Backfilling
        # Check if homepage candidate existed but was omitted by LLM
        clean_base = website_url.lower().rstrip("/") if website_url else ""
        home_candidate: CandidateURL | None = None
        for c in candidates:
            if categorize_candidate(c, base_url=clean_base) == SelectionCategory.HOME:
                home_candidate = c
                break

        if home_candidate is not None:
            home_norm = home_candidate.url.lower().rstrip("/")
            if home_norm not in seen_urls:
                if len(validated_pages) < max_pages:
                    # Prepend homepage to anchor brand positioning
                    validated_pages.insert(
                        0,
                        SelectedPage(
                            url=home_candidate.url,
                            page_type=home_candidate.page_type,
                            link_text=home_candidate.link_text,
                            source_section=home_candidate.source_section,
                            selection_reason="Homepage provides baseline brand identity, positioning, and overall navigation context.",
                        ),
                    )
                    seen_urls.add(home_norm)
                else:
                    # Replace lowest priority / redundant page with homepage
                    validated_pages.pop()
                    validated_pages.insert(
                        0,
                        SelectedPage(
                            url=home_candidate.url,
                            page_type=home_candidate.page_type,
                            link_text=home_candidate.link_text,
                            source_section=home_candidate.source_section,
                            selection_reason="Homepage provides baseline brand identity, positioning, and overall navigation context.",
                        ),
                    )
                    seen_urls.add(home_norm)

        # Backfill if LLM returned fewer than max_pages and candidates exist
        if len(validated_pages) < max_pages and len(candidates) > len(validated_pages):
            fallback_res = select_pages_deterministic(
                candidates,
                max_pages=max_pages,
                base_url=website_url,
                product_or_service=product_or_service,
            )
            for fb_page in fallback_res.selected_pages:
                if len(validated_pages) >= max_pages:
                    break
                fb_norm = fb_page.url.lower().rstrip("/")
                if fb_norm not in seen_urls:
                    seen_urls.add(fb_norm)
                    validated_pages.append(fb_page)

        logger.info(
            "OpenAI page selection successfully selected %d page(s) for %s.",
            len(validated_pages),
            website_url,
        )
        return PageSelectionResult(
            selected_pages=validated_pages,
            total_selected=len(validated_pages),
            selection_method="llm",
            model_used=selected_model,
            error_message=None,
        )

    except Exception as exc:
        logger.warning(
            "OpenAI page selection failed for %s (%s: %s); falling back to deterministic selection.",
            website_url,
            type(exc).__name__,
            exc,
        )
        res = select_pages_deterministic(candidates, max_pages=max_pages, base_url=website_url, product_or_service=product_or_service)
        res.error_message = f"OpenAI call failed ({type(exc).__name__}: {exc}); deterministic fallback used."
        return res


# ---------------------------------------------------------------------------
# Integrated Pipeline Orchestrator Interface
# ---------------------------------------------------------------------------

async def discover_and_select_pages(
    website_url: str,
    *,
    product_or_service: str | None = None,
    max_pages: int = DEFAULT_MAX_SELECTED_PAGES,
    model: str | None = None,
    min_priority_links: int = 5,
    force_sitemap: bool = False,
    timeout_seconds: int = 120,
) -> WebsitePipelineResult:
    """Execute end-to-end URL discovery and page selection for a business website.

    Workflow:
    1. Call :func:`discover_website_urls` with the business website URL.
    2. Receive the combined candidate URLs (homepage links + sitemap fallback).
    3. Pass candidate URLs to :func:`select_pages_with_llm` (with deterministic fallback).
    4. Return a structured :class:`WebsitePipelineResult` containing selected pages
       and a clean `final_urls` list ready for downstream crawler scraping.

    Args:
        website_url: Target business website URL.
        max_pages: Maximum number of pages to select for scraping.
        model: Optional LLM model identifier. Defaults to shared configuration (settings.LLM_MODEL).
        min_priority_links: Minimum priority links before inspecting main content.
        force_sitemap: Force sitemap discovery even if homepage coverage is sufficient.
        timeout_seconds: Timeout for initial homepage fetch.

    Returns:
        A populated :class:`WebsitePipelineResult` instance.
    """
    logger.info("Starting website discovery and page selection pipeline for %s.", website_url)

    # 1. Discover website URLs (Homepage + conditional sitemap fallback)
    discovery_result = await discover_website_urls(
        website_url,
        product_or_service=product_or_service,
        min_priority_links=min_priority_links,
        force_sitemap=force_sitemap,
        timeout_seconds=timeout_seconds,
    )

    # 2. Handle empty or failed discovery
    if not discovery_result.candidate_urls:
        logger.warning("No candidate URLs discovered for %s; pipeline ending with 0 pages.", website_url)
        selection_result = PageSelectionResult(
            selected_pages=[],
            total_selected=0,
            selection_method="empty",
            error_message="URL discovery produced zero candidate URLs.",
        )
        return WebsitePipelineResult(
            website_url=website_url,
            base_url=discovery_result.base_url or website_url,
            status="failed",
            discovery_result=discovery_result,
            selection_result=selection_result,
            final_urls=[],
            total_selected=0,
        )

    # 3. Select relevant pages via OpenAI (with deterministic fallback)
    selection_result = await select_pages_with_llm(
        candidates=discovery_result.candidate_urls,
        website_url=discovery_result.base_url or website_url,
        product_or_service=product_or_service,
        max_pages=max_pages,
        model=model,
    )

    final_urls = [page.url for page in selection_result.selected_pages]
    total_selected = len(final_urls)

    # 4. Determine overall pipeline status
    overall_status: Literal["success", "partial", "failed"] = discovery_result.status
    if total_selected == 0:
        overall_status = "failed"

    logger.info(
        "Website discovery and page selection finished for %s: status=%s, final_urls=%d (method=%s).",
        website_url,
        overall_status,
        total_selected,
        selection_result.selection_method,
    )

    return WebsitePipelineResult(
        website_url=website_url,
        base_url=discovery_result.base_url or website_url,
        status=overall_status,
        discovery_result=discovery_result,
        selection_result=selection_result,
        final_urls=final_urls,
        total_selected=total_selected,
    )
