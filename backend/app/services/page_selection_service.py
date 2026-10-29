"""OpenAI Page Selection Service.

Evaluates discovered candidate website URLs and selects the most relevant pages
for competitive marketing analysis using structured LLM output with a robust
deterministic fallback.

Responsibilities:
- Accept combined candidate URLs from `discover_website_urls()`.
- Use the shared canonical model configuration (`settings.LLM_MODEL`).
- Prioritize core marketing intelligence: About, Products/Services, Pricing,
  Offers/Promotions, and Key Features/Value Propositions.
- Enforce strict validation: every selected URL must exist in the candidate list,
  deduplicate selections, and never fabricate URLs.
- Provide a deterministic priority-weighted fallback if LLM is unavailable,
  unconfigured, or fails.
- Expose a clean pipeline interface `discover_and_select_pages()` connecting
  discovery to selection for downstream scraping.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from backend.app.core.config import settings
import re
from backend.app.agent.prompts import (
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
DEFAULT_MAX_SELECTED_PAGES: int = 8

# Upper bound on candidate URLs sent to LLM prompt to keep latency and token costs minimal
MAX_PROMPT_CANDIDATES: int = 60

# ---------------------------------------------------------------------------
# Priority Weights for Deterministic Fallback
# ---------------------------------------------------------------------------

PAGE_TYPE_PRIORITY: dict[PageType, int] = {
    PageType.PRICING: 100,
    PageType.PRODUCT: 90,
    PageType.SERVICE: 90,
    PageType.OFFER: 85,
    PageType.ABOUT: 75,
    PageType.FAQ: 60,
    PageType.CONTACT: 50,
    PageType.HOME: 45,
    PageType.BLOG: 35,
    PageType.OTHER: 25,
    PageType.POLICY: 15,
    PageType.UNKNOWN: 10,
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
# Deterministic Fallback Implementation
# ---------------------------------------------------------------------------


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

def select_pages_deterministic(
    candidates: list[CandidateURL],
    *,
    max_pages: int = DEFAULT_MAX_SELECTED_PAGES,
    base_url: str = "",
    product_or_service: str | None = None,
) -> PageSelectionResult:
    """Deterministically select up to `max_pages` candidate URLs based on business priorities.

    Selection rules:
    - Include the homepage if present among candidates or matching base_url.
    - Score and rank remaining candidates based on PageType priority (Pricing > Product/Service >
      Offers > About > FAQ > Contact > Blog > Policy).
    - Favor navigation and header links over footer and general sitemap links.
    - Deduplicate normalized URLs.
    - Never invent URLs; every selected page originates from `candidates`.

    Args:
        candidates: Discovered candidate URLs.
        max_pages: Maximum number of pages to select.
        base_url: Website base URL to identify the homepage.

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

    # 2. Identify and prioritize the homepage and primary about page
    clean_base = base_url.lower().rstrip("/") if base_url else ""
    homepage_candidate: CandidateURL | None = None
    about_candidate: CandidateURL | None = None
    other_candidates: list[CandidateURL] = []

    for c in unique_candidates:
        norm_url = c.url.lower().rstrip("/")
        parsed = urllib.parse.urlsplit(norm_url)
        path = parsed.path.strip("/")

        is_home = (
            c.page_type == PageType.HOME
            or (clean_base and norm_url == clean_base)
            or path in ("", "index.html", "index.php", "home")
        )

        if is_home and homepage_candidate is None:
            homepage_candidate = c
        elif c.page_type == PageType.ABOUT and about_candidate is None:
            about_candidate = c
        else:
            other_candidates.append(c)

    # 3. Score other candidates taking product relevance into account
    keywords = extract_product_keywords(product_or_service) if product_or_service else []
    product_phrase = product_or_service.strip() if product_or_service else ""
    has_matches = any(calculate_product_relevance(c, keywords, product_phrase) > 0 for c in other_candidates)

    def _calculate_score(candidate: CandidateURL) -> tuple[int, int, str]:
        type_score = PAGE_TYPE_PRIORITY.get(candidate.page_type, 10)
        section_score = SECTION_PRIORITY_BONUS.get(candidate.source_section, 0)
        relevance = calculate_product_relevance(candidate, keywords, product_phrase) if keywords else 0
        total_score = type_score + section_score + relevance

        # When matching product pages exist, penalize unrelated product pages
        if has_matches and relevance == 0 and candidate.page_type == PageType.PRODUCT:
            total_score -= 60

        # Shorter paths generally represent higher-level category pages
        path_segments = len([s for s in urllib.parse.urlsplit(candidate.url).path.split("/") if s])
        return (total_score, -path_segments, candidate.url)

    sorted_candidates = sorted(other_candidates, key=_calculate_score, reverse=True)

    # 4. Assemble selection up to max_pages
    selected_pages: list[SelectedPage] = []

    if homepage_candidate is not None:
        selected_pages.append(
            SelectedPage(
                url=homepage_candidate.url,
                page_type=homepage_candidate.page_type or PageType.HOME,
                link_text=homepage_candidate.link_text,
                source_section=homepage_candidate.source_section,
                selection_reason="Homepage provides baseline brand identity, positioning, and overall navigation context.",
            )
        )

    if about_candidate is not None and len(selected_pages) < max_pages:
        selected_pages.append(
            SelectedPage(
                url=about_candidate.url,
                page_type=about_candidate.page_type or PageType.ABOUT,
                link_text=about_candidate.link_text,
                source_section=about_candidate.source_section,
                selection_reason="About page provides foundational brand background, company story, and positioning context.",
            )
        )

    remaining_slots = max(0, max_pages - len(selected_pages))
    for c in sorted_candidates[:remaining_slots]:
        type_name = c.page_type.value if hasattr(c.page_type, "value") else str(c.page_type)
        rel = calculate_product_relevance(c, keywords, product_phrase) if keywords else 0
        if rel > 0:
            reason = f"Selected for target product relevance ({product_phrase}) from {c.source_section}."
        elif product_phrase and not has_matches:
            reason = f"Priority {type_name} page selected via deterministic hierarchy (no matching pages for '{product_phrase}' found)."
        else:
            reason = f"Priority {type_name} page selected via deterministic hierarchy from {c.source_section}."
        selected_pages.append(
            SelectedPage(
                url=c.url,
                page_type=c.page_type,
                link_text=c.link_text,
                source_section=c.source_section,
                selection_reason=reason,
            )
        )

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


async def select_pages_with_llm(
    candidates: list[CandidateURL],
    website_url: str,
    *,
    product_or_service: str | None = None,
    max_pages: int = DEFAULT_MAX_SELECTED_PAGES,
    model: str | None = None,
) -> PageSelectionResult:
    """Select up to `max_pages` relevant candidate URLs using structured OpenAI completion.

    Validates that:
    - Every selected URL exists in the input candidate list.
    - Duplicates are pruned.
    - Selections do not exceed `max_pages`.
    - If the LLM response is malformed, unavailable, or the API call fails,
      transparently falls back to :func:`select_pages_deterministic`.

    Args:
        candidates: Combined candidate URLs from URL discovery.
        website_url: Target business website URL.
        max_pages: Maximum number of pages to select.
        model: Optional LLM model identifier. Defaults to shared configuration (settings.LLM_MODEL).

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

    # 1. Validate OpenAI API Key
    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY is not configured; using deterministic fallback.")
        res = select_pages_deterministic(candidates, max_pages=max_pages, base_url=website_url, product_or_service=product_or_service)
        res.error_message = "OPENAI_API_KEY is not configured."
        return res

    raw_api_key = settings.OPENAI_API_KEY.get_secret_value().strip()
    if not raw_api_key or raw_api_key in ("your_openai_api_key_here", ""):
        logger.warning("OPENAI_API_KEY is set to a placeholder; using deterministic fallback.")
        res = select_pages_deterministic(candidates, max_pages=max_pages, base_url=website_url, product_or_service=product_or_service)
        res.error_message = "OPENAI_API_KEY is set to a placeholder value."
        return res

    # Resolve LLM model identifier (defaults to shared settings.LLM_MODEL)
    selected_model = model or settings.LLM_MODEL

    # 2. Build candidate lookups for fast, strict validation
    candidate_map: dict[str, CandidateURL] = {c.url: c for c in candidates}
    norm_candidate_map: dict[str, CandidateURL] = {c.url.lower().rstrip("/"): c for c in candidates}

    # 3. Format candidate URLs for the prompt (cap at MAX_PROMPT_CANDIDATES to keep context focused)
    keywords = extract_product_keywords(product_or_service) if product_or_service else []
    product_phrase = product_or_service.strip() if product_or_service else ""
    has_matches = any(calculate_product_relevance(c, keywords, product_phrase) > 0 for c in candidates)

    def _prompt_candidate_sort_key(c: CandidateURL) -> tuple[int, int, str]:
        type_score = PAGE_TYPE_PRIORITY.get(c.page_type, 10)
        section_score = SECTION_PRIORITY_BONUS.get(c.source_section, 0)
        relevance = calculate_product_relevance(c, keywords, product_phrase) if keywords else 0
        total_score = type_score + section_score + relevance

        # When matching product pages exist, deprioritize unrelated product pages
        if has_matches and relevance == 0 and c.page_type == PageType.PRODUCT:
            total_score -= 60

        path_segments = len([s for s in urllib.parse.urlsplit(c.url).path.split("/") if s])
        return (total_score, -path_segments, c.url)

    sorted_for_prompt = sorted(candidates, key=_prompt_candidate_sort_key, reverse=True)[:MAX_PROMPT_CANDIDATES]

    formatted_lines: list[str] = []
    for c in sorted_for_prompt:
        text_snippet = f' | Anchor: "{c.link_text}"' if c.link_text else ""
        type_str = c.page_type.value if hasattr(c.page_type, "value") else str(c.page_type)
        formatted_lines.append(f"- URL: {c.url} | Category: {type_str} | Found in: {c.source_section}{text_snippet}")

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
