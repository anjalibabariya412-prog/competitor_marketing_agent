"""Conditional sitemap discovery fallback service.

Provides fallback URL discovery for business websites when homepage fetching fails,
produces insufficient candidate URLs, or misses critical business page categories
(such as pricing, products/services, offers, and about pages).

Responsibilities:
- Inspect robots.txt for declared sitemap locations.
- Probe standard common sitemap locations when robots.txt yields no sitemaps.
- Parse standard XML sitemaps and nested sitemap indexes with bounded recursion.
- Handle compressed (.xml.gz) sitemaps, XML namespaces, and malformed XML safely.
- Normalize, deduplicate, filter (static assets, administrative/auth routes, external domains),
  and classify URLs into PageType enums using existing logic.
- Provide an integrated discovery pipeline combining homepage fetching and sitemap fallback.
"""

from __future__ import annotations

import gzip
import logging
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from backend.app.schemas.enums import PageType
from .homepage_service import HomepageFetchResult, fetch_homepage
from .url_extractor_service import (
    CandidateURL,
    _clean_and_resolve_url,
    classify_page_type,
    extract_candidate_urls,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants & Operational Limits
# ---------------------------------------------------------------------------

DEFAULT_REQUEST_TIMEOUT_SECONDS: float = 12.0
MAX_SITEMAP_FILES: int = 8
MAX_SITEMAP_DEPTH: int = 3
MAX_URLS_PER_SITEMAP: int = 500
MAX_TOTAL_SITEMAP_URLS: int = 1000

COMMON_SITEMAP_PATHS: tuple[str, ...] = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap/sitemap.xml",
    "/sitemaps/sitemap.xml",
    "/wp-sitemap.xml",
)

# Standard browser user-agent to avoid generic bot-blocking by basic WAFs
_USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Important core categories expected for competitive marketing analysis
IMPORTANT_PAGE_CATEGORIES: frozenset[str] = frozenset({
    "about",
    "pricing",
    "product_or_service",
    "offer",
})


# ---------------------------------------------------------------------------
# Result Models
# ---------------------------------------------------------------------------

class SitemapDiscoveryResult(BaseModel):
    """Structured result returned by standalone sitemap discovery."""

    base_url: str = Field(description="Base URL used for discovery and resolving relative links.")
    status: Literal["success", "no_sitemap_found", "empty_sitemap", "failed", "skipped"] = Field(
        description="Outcome status of the sitemap discovery operation."
    )
    message: str | None = Field(
        default=None,
        description="Informational or diagnostic message regarding discovery outcome.",
    )
    sitemaps_checked: list[str] = Field(
        default_factory=list,
        description="List of sitemap URLs that were requested/explored.",
    )
    candidate_urls: list[CandidateURL] = Field(
        default_factory=list,
        description="Cleaned, deduplicated candidate URLs discovered via sitemaps.",
    )
    total_found: int = Field(
        default=0,
        description="Total number of valid candidate URLs discovered from sitemaps.",
    )


class WebsiteURLDiscoveryResult(BaseModel):
    """Integrated discovery result combining homepage fetching and sitemap fallback."""

    requested_url: str = Field(description="Initial requested website URL.")
    base_url: str = Field(description="Canonical discovery base URL.")
    status: Literal["success", "partial", "failed"] = Field(
        description="Overall discovery outcome status."
    )
    homepage_status: str | None = Field(
        default=None,
        description="Status of the initial homepage fetch operation.",
    )
    homepage_candidate_count: int = Field(
        default=0,
        description="Count of candidate URLs extracted directly from homepage HTML.",
    )
    sitemap_triggered: bool = Field(
        default=False,
        description="Whether the sitemap discovery fallback was triggered.",
    )
    sitemap_trigger_reason: str | None = Field(
        default=None,
        description="Reason why sitemap fallback was triggered or skipped.",
    )
    sitemap_status: str | None = Field(
        default=None,
        description="Status of the sitemap fallback operation if executed.",
    )
    sitemap_candidate_count: int = Field(
        default=0,
        description="Count of new candidate URLs added via sitemap discovery.",
    )
    candidate_urls: list[CandidateURL] = Field(
        default_factory=list,
        description="Combined, deduplicated candidate URLs ready for downstream scraping.",
    )
    total_candidates: int = Field(
        default=0,
        description="Total candidate URLs discovered across all mechanisms.",
    )
    covered_categories: list[str] = Field(
        default_factory=list,
        description="List of important business page categories covered by the candidates.",
    )
    missing_categories: list[str] = Field(
        default_factory=list,
        description="List of important business page categories still missing.",
    )


# ---------------------------------------------------------------------------
# Fallback Trigger Evaluation
# ---------------------------------------------------------------------------

def check_category_coverage(candidate_urls: list[CandidateURL]) -> dict[str, bool]:
    """Check which of the core business categories are present among candidate URLs.

    Evaluates URL classifications heuristically against key marketing categories:
    - about: PageType.ABOUT
    - pricing: PageType.PRICING
    - product_or_service: PageType.PRODUCT or PageType.SERVICE
    - offer: PageType.OFFER
    """
    present_types = {c.page_type for c in candidate_urls}
    return {
        "about": PageType.ABOUT in present_types,
        "pricing": PageType.PRICING in present_types,
        "product_or_service": (PageType.PRODUCT in present_types or PageType.SERVICE in present_types),
        "offer": PageType.OFFER in present_types,
    }


def should_trigger_sitemap_fallback(
    homepage_result: HomepageFetchResult | None = None,
    candidate_urls: list[CandidateURL] | None = None,
) -> tuple[bool, str]:
    """Evaluate whether sitemap discovery fallback should be triggered.

    Conditions evaluated:
    - Case A: Homepage fetching failed, was blocked, returned an empty response,
              or was not provided.
    - Case B: Homepage fetching succeeded, but 0 useful non-homepage candidate URLs
              were extracted.
    - Case C: Homepage candidates exist, but one or more critical business page
              categories (about, pricing, product/service, offer) are missing.

    Returns:
        (should_trigger: bool, reason: str)
    """
    # Case A: Homepage fetch failed or unusable
    if homepage_result is None:
        return True, "homepage_not_provided"

    if homepage_result.status in ("failed", "blocked"):
        return True, f"homepage_fetch_{homepage_result.status}"

    if homepage_result.status == "empty_response" or not homepage_result.html:
        return True, "homepage_fetch_empty_response"

    # Case B: No useful non-homepage candidate URLs extracted
    candidates = candidate_urls or []
    useful_candidates = [c for c in candidates if c.page_type != PageType.HOME]
    if not useful_candidates:
        return True, "no_useful_urls_extracted"

    # Case C: Important business page categories missing
    coverage = check_category_coverage(candidates)
    missing = [cat for cat, covered in coverage.items() if not covered]

    if missing:
        return True, f"missing_important_categories: {', '.join(missing)}"

    return False, "all_important_categories_covered"


# ---------------------------------------------------------------------------
# XML Parsing & Link Extraction
# ---------------------------------------------------------------------------

def _parse_xml_content(content_bytes: bytes) -> ET.Element | None:
    """Parse raw bytes into an ElementTree root element, handling gzip and encoding safely."""
    # Decompress gzip magic header (0x1f 0x8b) if present
    if content_bytes.startswith(b"\x1f\x8b"):
        try:
            content_bytes = gzip.decompress(content_bytes)
        except Exception as exc:
            logger.debug("Failed to decompress gzip content: %s", exc)
            return None

    # Strip XML UTF-8 BOM if present
    if content_bytes.startswith(b"\xef\xbb\xbf"):
        content_bytes = content_bytes[3:]

    try:
        return ET.fromstring(content_bytes)
    except ET.ParseError:
        try:
            # Fallback: decode with replacement and trim leading non-XML characters
            text = content_bytes.decode("utf-8", errors="replace")
            idx = text.find("<")
            if idx > 0:
                text = text[idx:]
            return ET.fromstring(text.encode("utf-8"))
        except Exception as exc:
            logger.debug("ElementTree failed to parse XML: %s", exc)
            return None


def _extract_sitemap_elements(root: ET.Element) -> tuple[list[str], list[str]]:
    """Inspect XML elements to separate sub-sitemap URLs from page URLs.

    Returns:
        (sub_sitemap_urls, page_urls)
    """
    sub_sitemaps: list[str] = []
    page_urls: list[str] = []

    root_local = root.tag.split("}")[-1].lower()

    if root_local == "sitemapindex":
        for sitemap_elem in root:
            if sitemap_elem.tag.split("}")[-1].lower() == "sitemap":
                for child in sitemap_elem:
                    if child.tag.split("}")[-1].lower() == "loc" and child.text:
                        loc = child.text.strip()
                        if loc:
                            sub_sitemaps.append(loc)
    elif root_local == "urlset":
        for url_elem in root:
            if url_elem.tag.split("}")[-1].lower() == "url":
                for child in url_elem:
                    if child.tag.split("}")[-1].lower() == "loc" and child.text:
                        loc = child.text.strip()
                        if loc:
                            page_urls.append(loc)
    else:
        # Generic fallback for non-standard XML tag hierarchies
        for parent in root.iter():
            p_tag = parent.tag.split("}")[-1].lower()
            if p_tag == "sitemap":
                for child in parent:
                    if child.tag.split("}")[-1].lower() == "loc" and child.text:
                        loc = child.text.strip()
                        if loc:
                            sub_sitemaps.append(loc)
            elif p_tag == "url":
                for child in parent:
                    if child.tag.split("}")[-1].lower() == "loc" and child.text:
                        loc = child.text.strip()
                        if loc:
                            page_urls.append(loc)

    return sub_sitemaps, page_urls


# ---------------------------------------------------------------------------
# Discovery Mechanisms (robots.txt & common probes)
# ---------------------------------------------------------------------------

async def _discover_sitemaps_from_robots(
    client: httpx.AsyncClient,
    base_url: str,
) -> list[str]:
    """Fetch robots.txt and extract all declared Sitemap directives."""
    parsed = urllib.parse.urlsplit(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    discovered: list[str] = []

    try:
        resp = await client.get(robots_url)
        if resp.status_code == 200 and resp.text:
            for line in resp.text.splitlines():
                line = line.strip()
                if line.lower().startswith("sitemap:"):
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        raw_loc = parts[1].strip()
                        if raw_loc:
                            resolved = urllib.parse.urljoin(robots_url, raw_loc)
                            if resolved.lower().startswith(("http://", "https://")):
                                discovered.append(resolved)
    except Exception as exc:
        logger.debug("Robots.txt check failed for %s: %s", robots_url, exc)

    return discovered


def _get_common_sitemap_probe_urls(base_url: str) -> list[str]:
    """Return common sitemap URLs to check when robots.txt yields no sitemaps."""
    parsed = urllib.parse.urlsplit(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return [urllib.parse.urljoin(origin, path) for path in COMMON_SITEMAP_PATHS]


# ---------------------------------------------------------------------------
# Core Sitemap Discovery Service
# ---------------------------------------------------------------------------

async def discover_sitemap_urls(
    website_url: str,
    *,
    timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    max_files: int = MAX_SITEMAP_FILES,
    max_depth: int = MAX_SITEMAP_DEPTH,
    max_urls: int = MAX_TOTAL_SITEMAP_URLS,
) -> SitemapDiscoveryResult:
    """Discover, fetch, and parse XML sitemaps for candidate internal website URLs.

    Examines robots.txt first, falling back to standard probe paths if no sitemaps
    are declared. Handles nested sitemap indexes with bounded depth and file limits.

    Args:
        website_url: Base or homepage URL of the business website.
        timeout_seconds: HTTP client timeout per request.
        max_files: Maximum number of sitemap XML files to fetch.
        max_depth: Maximum recursion depth for sitemap indexes.
        max_urls: Maximum total candidate URLs to return.

    Returns:
        A populated :class:`SitemapDiscoveryResult` instance.
    """
    parsed = urllib.parse.urlsplit(website_url)
    if not parsed.scheme or not parsed.netloc:
        return SitemapDiscoveryResult(
            base_url=website_url,
            status="failed",
            message=f"Invalid website URL for sitemap discovery: '{website_url}'.",
        )

    base_url = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/xml, text/xml, */*"}

    sitemaps_to_explore: list[tuple[str, int]] = []
    seen_sitemaps: set[str] = set()
    sitemaps_checked: list[str] = []

    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        follow_redirects=True,
        headers=headers,
    ) as client:
        # 1. Discover sitemaps declared in robots.txt
        robots_sitemaps = await _discover_sitemaps_from_robots(client, base_url)
        for sm in robots_sitemaps:
            sitemaps_to_explore.append((sm, 1))

        # 2. If robots.txt had no sitemaps, probe common locations
        probed_common = False
        if not sitemaps_to_explore:
            probed_common = True
            for sm in _get_common_sitemap_probe_urls(base_url):
                sitemaps_to_explore.append((sm, 1))

        total_files_fetched = 0
        discovered_candidates: list[CandidateURL] = []
        seen_candidate_urls: set[str] = set()

        while (
            (sitemaps_to_explore or (not discovered_candidates and not probed_common))
            and total_files_fetched < max_files
            and len(discovered_candidates) < max_urls
        ):
            # If declared sitemaps were exhausted without finding any valid candidate URLs,
            # fall back to probing common sitemap locations.
            if not sitemaps_to_explore:
                if not discovered_candidates and not probed_common and total_files_fetched < max_files:
                    probed_common = True
                    for sm in _get_common_sitemap_probe_urls(base_url):
                        norm_key = sm.lower().rstrip("/")
                        if norm_key not in seen_sitemaps:
                            sitemaps_to_explore.append((sm, 1))

                if not sitemaps_to_explore:
                    break

            target_sitemap_url, depth = sitemaps_to_explore.pop(0)
            norm_sitemap_key = target_sitemap_url.lower().rstrip("/")
            if norm_sitemap_key in seen_sitemaps:
                continue
            seen_sitemaps.add(norm_sitemap_key)
            sitemaps_checked.append(target_sitemap_url)

            try:
                resp = await client.get(target_sitemap_url)
                if resp.status_code != 200 or not resp.content:
                    continue

                total_files_fetched += 1
                root = _parse_xml_content(resp.content)
                if root is None:
                    continue

                sub_sitemaps, raw_page_urls = _extract_sitemap_elements(root)

                # Queue nested sitemaps within depth bounds
                if depth < max_depth:
                    for sub in sub_sitemaps:
                        sub_key = sub.lower().rstrip("/")
                        if sub_key not in seen_sitemaps:
                            sitemaps_to_explore.append((sub, depth + 1))

                # Clean, filter, classify, and deduplicate page URLs
                for raw_url in raw_page_urls:
                    if len(discovered_candidates) >= max_urls:
                        break

                    clean_url = _clean_and_resolve_url(raw_url, base_url)
                    if not clean_url:
                        continue

                    norm_candidate = clean_url.rstrip("/")
                    if norm_candidate in seen_candidate_urls:
                        continue
                    seen_candidate_urls.add(norm_candidate)

                    page_type = classify_page_type(clean_url, "")
                    discovered_candidates.append(
                        CandidateURL(
                            url=clean_url,
                            link_text="",
                            page_type=page_type,
                            source_section="sitemap",
                        )
                    )

            except httpx.HTTPError as exc:
                logger.debug("HTTP error fetching sitemap '%s': %s", target_sitemap_url, exc)
            except Exception as exc:
                logger.debug("Unexpected error processing sitemap '%s': %s", target_sitemap_url, exc)

    count = len(discovered_candidates)
    if count > 0:
        logger.info(
            "Sitemap discovery found %d candidate URLs across %d sitemap files for %s.",
            count,
            total_files_fetched,
            base_url,
        )
        return SitemapDiscoveryResult(
            base_url=base_url,
            status="success",
            message=f"Discovered {count} candidate URLs from {total_files_fetched} sitemap file(s).",
            sitemaps_checked=sitemaps_checked,
            candidate_urls=discovered_candidates,
            total_found=count,
        )

    if total_files_fetched > 0:
        return SitemapDiscoveryResult(
            base_url=base_url,
            status="empty_sitemap",
            message="Sitemap files were found and parsed, but contained no valid internal URLs.",
            sitemaps_checked=sitemaps_checked,
            candidate_urls=[],
            total_found=0,
        )

    return SitemapDiscoveryResult(
        base_url=base_url,
        status="no_sitemap_found",
        message="No accessible XML sitemaps found via robots.txt or common paths.",
        sitemaps_checked=sitemaps_checked,
        candidate_urls=[],
        total_found=0,
    )


# ---------------------------------------------------------------------------
# Merging Candidate URLs
# ---------------------------------------------------------------------------

def merge_candidate_urls(
    homepage_candidates: list[CandidateURL],
    sitemap_candidates: list[CandidateURL],
    base_url: str,
) -> list[CandidateURL]:
    """Merge and deduplicate homepage and sitemap candidate URLs.

    Preserves existing homepage candidates, their link text, and priority
    classifications. Appends newly discovered sitemap candidates that do not
    already exist in the set.

    Args:
        homepage_candidates: Candidate URLs extracted from homepage HTML.
        sitemap_candidates: Candidate URLs discovered via sitemaps.
        base_url: Website base URL for normalisation.

    Returns:
        Deduplicated list of merged CandidateURL objects.
    """
    merged: list[CandidateURL] = []
    seen_urls: set[str] = set()

    # 1. Add homepage candidates first to preserve their priority and link text
    for c in homepage_candidates:
        norm_key = c.url.rstrip("/")
        if norm_key not in seen_urls:
            seen_urls.add(norm_key)
            merged.append(c)

    # 2. Add sitemap candidates if not already present
    for s in sitemap_candidates:
        norm_key = s.url.rstrip("/")
        if norm_key not in seen_urls:
            seen_urls.add(norm_key)
            merged.append(s)

    return merged


# ---------------------------------------------------------------------------
# High-Level Orchestrator
# ---------------------------------------------------------------------------

async def discover_website_urls(
    website_url: str,
    *,
    homepage_fetch_result: HomepageFetchResult | None = None,
    min_priority_links: int = 5,
    force_sitemap: bool = False,
    timeout_seconds: int = 120,
) -> WebsiteURLDiscoveryResult:
    """Execute the complete URL discovery workflow with conditional sitemap fallback.

    Step 1: Attempt homepage fetching (or use provided HomepageFetchResult).
    Step 2: If successful, extract and classify homepage links.
    Step 3: Check whether sitemap fallback is required (Cases A, B, C).
    Step 4: If needed, discover and parse sitemaps independently.
    Step 5: Merge and deduplicate candidates, retaining homepage candidates.
    Step 6: Return the combined structured result.

    Args:
        website_url: Business website URL to analyze.
        homepage_fetch_result: Optional pre-fetched homepage result to avoid re-fetching.
        min_priority_links: Priority links threshold before inspecting main content.
        force_sitemap: When True, runs sitemap fallback unconditionally.
        timeout_seconds: Timeout for initial homepage fetch if executed.

    Returns:
        A populated :class:`WebsiteURLDiscoveryResult` instance.
    """
    # 1. Homepage Fetching
    if homepage_fetch_result is None:
        logger.info("Initiating homepage fetch for %s.", website_url)
        homepage_fetch_result = await fetch_homepage(website_url, timeout_seconds=timeout_seconds)

    resolved_base: str = (
        homepage_fetch_result.final_url
        or homepage_fetch_result.requested_url
        or website_url
    )

    # 2. Homepage URL Extraction (if fetch succeeded)
    homepage_candidates: list[CandidateURL] = []
    if homepage_fetch_result.status == "success" and homepage_fetch_result.html:
        extraction_res = extract_candidate_urls(
            homepage_fetch_result,
            base_url=resolved_base,
            min_priority_links=min_priority_links,
        )
        homepage_candidates = extraction_res.candidate_urls

    homepage_candidate_count = len(homepage_candidates)

    # 3. Check Fallback Trigger
    should_fallback, trigger_reason = should_trigger_sitemap_fallback(
        homepage_fetch_result,
        homepage_candidates,
    )
    run_sitemap = should_fallback or force_sitemap
    if force_sitemap and not should_fallback:
        trigger_reason = "force_sitemap_requested"

    # 4. Discover and Parse Sitemaps if triggered
    sitemap_candidates: list[CandidateURL] = []
    sitemap_status: str | None = None

    if run_sitemap:
        logger.info(
            "Triggering sitemap fallback for %s (reason: %s).",
            resolved_base,
            trigger_reason,
        )
        sitemap_res = await discover_sitemap_urls(resolved_base)
        sitemap_status = sitemap_res.status
        sitemap_candidates = sitemap_res.candidate_urls
    else:
        sitemap_status = "skipped"

    # 5. Merge and deduplicate candidates
    combined_candidates = merge_candidate_urls(
        homepage_candidates,
        sitemap_candidates,
        base_url=resolved_base,
    )
    total_candidates = len(combined_candidates)

    # 6. Assess category coverage
    coverage = check_category_coverage(combined_candidates)
    covered = [cat for cat, ok in coverage.items() if ok]
    missing = [cat for cat, ok in coverage.items() if not ok]

    # Determine overall status
    if total_candidates > 0:
        overall_status: Literal["success", "partial", "failed"] = (
            "success" if homepage_fetch_result.status == "success" else "partial"
        )
    else:
        overall_status = "failed"

    logger.info(
        "Website URL discovery finished for %s: status=%s, total=%d (homepage=%d, sitemap=%d).",
        resolved_base,
        overall_status,
        total_candidates,
        homepage_candidate_count,
        len(sitemap_candidates),
    )

    return WebsiteURLDiscoveryResult(
        requested_url=website_url,
        base_url=resolved_base,
        status=overall_status,
        homepage_status=homepage_fetch_result.status,
        homepage_candidate_count=homepage_candidate_count,
        sitemap_triggered=run_sitemap,
        sitemap_trigger_reason=trigger_reason,
        sitemap_status=sitemap_status,
        sitemap_candidate_count=len(sitemap_candidates),
        candidate_urls=combined_candidates,
        total_candidates=total_candidates,
        covered_categories=covered,
        missing_categories=missing,
    )
