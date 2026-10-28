"""Business Overview Generation Service.

Converts successfully crawled website pages into a concise, structured,
and strictly evidence-based business overview for downstream competitor analysis.

Key Responsibilities:
- Accept crawled content from :class:`WebsiteCrawlResult` or a sequence of :class:`ScrapedPage` models.
- Validate that sufficient, usable content exists before invoking any LLM.
- Ground all facts, products, services, features, and positioning in the provided website text.
- Enforce strict accuracy: no hallucinated facts, prices, audience claims, or marketing advice.
- Distinguish between explicit statements and inferred observations.
- Preserve source URLs on every finding and item to ensure full traceability.
- Identify and catalog information gaps (e.g. unstated pricing, unknown audience).
- Use the shared canonical model configuration (``settings.LLM_MODEL`` = ``gpt-6-luna``).
- Bound token usage by applying strict per-page and total input character limits.
- Return a structured :class:`BusinessOverviewResult` leveraging existing schemas (:class:`Finding`).
"""

from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Any, Literal, Sequence

from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field

try:
    from app.core.config import settings
    from app.schemas.enums import FindingType, PageType
    from app.schemas.scraped_data import Finding, ScrapedPage
    from app.services.website_content_crawler_service import WebsiteCrawlResult
except ModuleNotFoundError:
    from backend.app.core.config import settings
    from backend.app.schemas.enums import FindingType, PageType
    from backend.app.schemas.scraped_data import Finding, ScrapedPage
    from backend.app.services.website_content_crawler_service import WebsiteCrawlResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Operational & Cost Control Limits
# ---------------------------------------------------------------------------

# Maximum characters allowed per individual page to avoid runaway context
MAX_PAGE_INPUT_CHARS: int = 6_000

# Total character budget across all pages passed to the LLM prompt (~7,500 tokens)
MAX_TOTAL_INPUT_CHARS: int = 30_000

# Minimum total characters required across all pages to warrant an LLM generation call
MIN_TOTAL_CONTENT_CHARS: int = 150

# Request timeout for OpenAI LLM call
DEFAULT_LLM_TIMEOUT_SECONDS: float = 60.0

# ---------------------------------------------------------------------------
# Prompt Definitions
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert business intelligence and competitor analysis analyst.
Your task is to analyze the crawled website content of a business and generate a concise, factual, and strictly evidence-based business overview.

STRICT ACCURACY RULES:
1. EVIDENCE-BASED ONLY: Use ONLY facts, statements, products, and claims explicitly stated or clearly demonstrated in the provided page excerpts.
2. NO HALLUCINATION: Never invent business facts, revenue, prices, target audiences, features, partners, or claims that are not in the text.
3. EXPLICIT VS INFERRED: Distinguish clearly between explicit statements (directly stated on the website) and reasonable interpretations. Set is_explicit=True only when directly stated.
4. UNKNOWN INFORMATION: If key information (such as pricing, specific customer personas, company history, or contact details) is not mentioned on the provided pages, explicitly record it in `information_gaps`. Do not guess.
5. FAILED / MISSING PAGES: Do not treat failed or blocked pages as evidence that information does not exist. Only report what could or could not be determined from the available text.
6. NO RECOMMENDATIONS: Do NOT provide marketing advice, growth strategies, or suggestions. Focus entirely on describing what the business is and does today.
7. SOURCE TRACEABILITY:
   - Every product, value proposition, target audience, and key finding MUST cite the EXACT page URL from the provided crawled pages list where the supporting evidence appears.
   - NEVER cite URLs that are not in the provided crawled pages list (do NOT invent internal paths or cite external websites).
   - NEVER attribute a claim to the homepage unless the homepage excerpt actually contains evidence for that specific claim.
   - If a claim or observation cannot be linked to a specific provided page, do NOT invent a URL; set source_url to null.
"""

_USER_PROMPT_TEMPLATE = """\
Analyze the following crawled pages for website: {website_url}

=== CRAWLED WEBSITE PAGES ({page_count} pages) ===
{formatted_pages}

Generate a comprehensive, structured Business Overview conforming strictly to the required schema. Ensure all source URLs match the pages above.
"""


# ---------------------------------------------------------------------------
# Structured Output Models
# ---------------------------------------------------------------------------

class ProductServiceItem(BaseModel):
    """Represents a discrete product, service, or solution offered by the business."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(description="Name or title of the product or service.")
    description: str = Field(description="Factual description based strictly on the website text.")
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
        description="Specific page URL providing evidence for this product or service, or None if unavailable or unverified.",
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
        description="Primary products, services, or solutions offered by the business.",
    )
    target_audience: TargetAudienceInfo = Field(
        description="Target customer or audience analysis with explicit vs inferred distinction.",
    )
    positioning_and_value_propositions: list[ValueProposition] = Field(
        default_factory=list,
        description="Key positioning claims, value propositions, or differentiators.",
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
# Helper Functions
# ---------------------------------------------------------------------------

def _extract_usable_pages(
    crawl_input: WebsiteCrawlResult | Sequence[ScrapedPage],
) -> tuple[list[ScrapedPage], str]:
    """Extract and validate non-empty ScrapedPage models and infer the base website URL.

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


def _format_pages_for_prompt(pages: list[ScrapedPage]) -> str:
    """Format and budget page excerpts for the LLM prompt.

    Enforces per-page limits and an overall budget to avoid context window bloat.
    """
    total_chars = 0
    formatted_chunks: list[str] = []

    for idx, page in enumerate(pages, start=1):
        if total_chars >= MAX_TOTAL_INPUT_CHARS:
            break

        title_str = f" | Title: {page.title}" if page.title else ""
        type_str = f" | Type: {page.page_type.value}" if hasattr(page.page_type, "value") else ""
        header = f"--- Page {idx}: {page.url}{title_str}{type_str} ---"

        # Limit per-page content
        page_text = page.content.strip()
        if len(page_text) > MAX_PAGE_INPUT_CHARS:
            page_text = page_text[:MAX_PAGE_INPUT_CHARS] + "\n[Content truncated for length...]"

        chunk = f"{header}\n{page_text}\n"

        # Check total budget
        if total_chars + len(chunk) > MAX_TOTAL_INPUT_CHARS:
            allowed_chars = MAX_TOTAL_INPUT_CHARS - total_chars
            if allowed_chars > 200:
                chunk = chunk[:allowed_chars] + "\n[Remaining content truncated for budget...]\n"
                formatted_chunks.append(chunk)
            break

        formatted_chunks.append(chunk)
        total_chars += len(chunk)

    return "\n".join(formatted_chunks)


def _normalize_url_for_comparison(url: str) -> str:
    """Normalize a URL for robust comparison across schemes, trailing slashes, and default ports."""
    cleaned = url.strip()
    if not cleaned:
        return ""
    try:
        parsed = urllib.parse.urlsplit(cleaned)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        # Remove standard default ports
        if ":" in netloc:
            host, port = netloc.split(":", 1)
            if (scheme == "http" and port == "80") or (scheme == "https" and port == "443"):
                netloc = host
        path = parsed.path or "/"
        # Normalize non-root trailing slash
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")
        return urllib.parse.urlunsplit((scheme, netloc, path, "", ""))
    except Exception:
        return cleaned.lower().rstrip("/")


def _resolve_and_verify_source_url(
    raw_url: str | None,
    norm_to_original: dict[str, str],
    base_website_url: str,
) -> str | None:
    """Validate and resolve a source URL against the set of actually crawled pages.

    Returns the exact original crawled URL if matched, or None if the URL was not
    crawled, is external, or is invalid. NEVER falls back to the homepage.
    """
    if not raw_url or not isinstance(raw_url, str):
        return None

    cleaned = raw_url.strip()
    if not cleaned:
        return None

    # If relative path cited (e.g. '/pricing' or 'about'), resolve against base website URL
    parsed = urllib.parse.urlsplit(cleaned)
    if not parsed.scheme or not parsed.netloc:
        cleaned = urllib.parse.urljoin(base_website_url, cleaned)
        parsed = urllib.parse.urlsplit(cleaned)

    # Must use http or https
    if parsed.scheme.lower() not in ("http", "https"):
        return None

    # Reject external domains not matching crawled website host
    base_host = urllib.parse.urlsplit(base_website_url).netloc.lower()
    cited_host = parsed.netloc.lower()
    if ":" in base_host:
        base_host = base_host.split(":")[0]
    if ":" in cited_host:
        cited_host = cited_host.split(":")[0]

    # Domain check: must match base host or be a valid subdomain
    if (
        cited_host != base_host
        and not cited_host.endswith("." + base_host)
        and not base_host.endswith("." + cited_host)
    ):
        logger.debug("Rejecting external source URL '%s' (base host: %s).", cleaned, base_host)
        return None

    norm = _normalize_url_for_comparison(cleaned)
    if norm in norm_to_original:
        return norm_to_original[norm]

    # Also check path-only comparison without query
    norm_no_query = _normalize_url_for_comparison(
        urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", ""))
    )
    if norm_no_query in norm_to_original:
        return norm_to_original[norm_no_query]

    # Un-crawled fabricated path or unmatched internal URL -> reject!
    logger.debug("Rejecting un-crawled internal source URL '%s'.", cleaned)
    return None


def _sanitize_and_validate_overview(
    overview: BusinessOverview,
    valid_source_urls: Sequence[str] | set[str] | Sequence[ScrapedPage],
    base_website_url: str | None = None,
    fallback_website_url: str | None = None,
) -> BusinessOverview:
    """Verify and normalize source URLs against the actual crawled pages.

    Strict Attribution Rules:
    - Never replace an un-crawled, invalid, or external source URL with the homepage.
    - If a product/service or value proposition source URL is invalid or un-crawled,
      mark its source_url as None (representing evidence as unavailable).
    - If a target audience or value proposition claim had is_explicit=True but its source
      cannot be verified, set is_explicit=False.
    - For key_findings, since Finding requires a verified URL, remove unsupported findings.
    - sources_used is compiled strictly from verified, non-null source URLs.
    """
    resolved_base = base_website_url or fallback_website_url or overview.website_url or "https://unknown.com"
    if not overview.website_url:
        overview.website_url = resolved_base

    # Build canonical lookup table from crawled pages
    norm_to_original: dict[str, str] = {}
    for item in valid_source_urls:
        url_str = item.url if isinstance(item, ScrapedPage) else item
        if url_str and isinstance(url_str, str):
            norm = _normalize_url_for_comparison(url_str)
            norm_to_original[norm] = url_str
            # Also map without query parameters
            parsed_u = urllib.parse.urlsplit(url_str)
            norm_no_q = _normalize_url_for_comparison(
                urllib.parse.urlunsplit((parsed_u.scheme.lower(), parsed_u.netloc.lower(), parsed_u.path, "", ""))
            )
            if norm_no_q not in norm_to_original:
                norm_to_original[norm_no_q] = url_str

    # 1. Sanitize product/service source URLs
    for p in overview.products_and_services:
        verified_source = _resolve_and_verify_source_url(
            p.source_url, norm_to_original, resolved_base
        )
        p.source_url = verified_source

    # 2. Sanitize value propositions
    for vp in overview.positioning_and_value_propositions:
        verified_source = _resolve_and_verify_source_url(
            vp.source_url, norm_to_original, resolved_base
        )
        vp.source_url = verified_source
        if verified_source is None and vp.is_explicit:
            vp.is_explicit = False

    # 3. Sanitize target audience
    verified_audience_source = _resolve_and_verify_source_url(
        overview.target_audience.source_url, norm_to_original, resolved_base
    )
    overview.target_audience.source_url = verified_audience_source
    if verified_audience_source is None and overview.target_audience.is_explicit:
        overview.target_audience.is_explicit = False

    # 4. Sanitize key findings (remove findings citing un-crawled/unverified URLs)
    verified_findings: list[Finding] = []
    for finding in overview.key_findings:
        verified_source = _resolve_and_verify_source_url(
            finding.source_url, norm_to_original, resolved_base
        )
        if verified_source is not None:
            finding.source_url = verified_source
            verified_findings.append(finding)
        else:
            logger.warning(
                "Dropping unsupported finding '%s' citing un-crawled source: %s",
                finding.statement[:60],
                finding.source_url,
            )
    overview.key_findings = verified_findings

    # 5. Compile sources_used strictly from verified sources
    verified_sources: set[str] = set()
    for p in overview.products_and_services:
        if p.source_url:
            verified_sources.add(p.source_url)
    for vp in overview.positioning_and_value_propositions:
        if vp.source_url:
            verified_sources.add(vp.source_url)
    for f in overview.key_findings:
        if f.source_url:
            verified_sources.add(f.source_url)
    if overview.target_audience.source_url:
        verified_sources.add(overview.target_audience.source_url)

    overview.sources_used = sorted(list(verified_sources))
    return overview


# ---------------------------------------------------------------------------
# Public Service API
# ---------------------------------------------------------------------------

async def generate_business_overview(
    crawl_input: WebsiteCrawlResult | Sequence[ScrapedPage],
    *,
    website_url: str | None = None,
    model: str | None = None,
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
) -> BusinessOverviewResult:
    """Generate a structured, evidence-based business overview from crawled website content.

    Args:
        crawl_input: The crawled website output, either a :class:`WebsiteCrawlResult`
            or a sequence of :class:`ScrapedPage` instances.
        website_url: Optional root website URL. Inferred from crawl_input if omitted.
        model: Optional LLM model identifier. Defaults to shared configuration (settings.LLM_MODEL).
        timeout_seconds: Maximum seconds to wait for LLM completion.

    Returns:
        A populated :class:`BusinessOverviewResult` containing the overview and metadata.
    """
    start_time = time.monotonic()

    # 1. Extract and validate usable scraped pages
    usable_pages, inferred_url = _extract_usable_pages(crawl_input)
    resolved_website_url = website_url or inferred_url or "https://unknown.com"

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
    logger.info(
        "Initiating business overview generation for %s using model '%s' (%d pages, %d chars).",
        resolved_website_url,
        resolved_model,
        len(usable_pages),
        total_content_length,
    )

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

    # 5. Build prompt with bounded page excerpts
    formatted_pages = _format_pages_for_prompt(usable_pages)
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        website_url=resolved_website_url,
        page_count=len(usable_pages),
        formatted_pages=formatted_pages,
    )

    # 6. Invoke OpenAI Structured Output
    try:
        client = AsyncOpenAI(api_key=raw_api_key, timeout=timeout_seconds)

        completion = await client.beta.chat.completions.parse(
            model=resolved_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
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
                total_pages_analyzed=len(usable_pages),
                total_findings=0,
                model_used=resolved_model,
                execution_time_seconds=round(time.monotonic() - start_time, 2),
                error_message=err,
            )

        # 7. Validate and sanitize source URLs against actual crawled pages
        valid_source_urls = {p.url for p in usable_pages}
        sanitized_overview = _sanitize_and_validate_overview(
            parsed_overview,
            valid_source_urls=valid_source_urls,
            fallback_website_url=resolved_website_url,
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
            total_pages_analyzed=len(usable_pages),
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
