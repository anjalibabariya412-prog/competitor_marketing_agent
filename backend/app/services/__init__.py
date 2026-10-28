"""Services package for the application."""

from .homepage_service import HomepageFetchResult, fetch_homepage
from .page_selection_service import (
    PageSelectionDecision,
    PageSelectionResult,
    SelectedPage,
    WebsitePipelineResult,
    discover_and_select_pages,
    select_pages_deterministic,
    select_pages_with_llm,
)
from .sitemap_fallback_service import (
    SitemapDiscoveryResult,
    WebsiteURLDiscoveryResult,
    check_category_coverage,
    discover_sitemap_urls,
    discover_website_urls,
    merge_candidate_urls,
    should_trigger_sitemap_fallback,
)
from .url_extractor_service import (
    CandidateURL,
    URLExtractionResult,
    classify_page_type,
    extract_candidate_urls,
)
from .website_content_crawler_service import (
    FailedPageScrape,
    WebsiteCrawlResult,
    crawl_selected_pages,
    crawl_website_content,
)
from .business_overview_service import (
    BusinessOverview,
    BusinessOverviewResult,
    InformationGap,
    ProductServiceItem,
    TargetAudienceInfo,
    ValueProposition,
    generate_business_overview,
)

__all__ = [
    "BusinessOverview",
    "BusinessOverviewResult",
    "CandidateURL",
    "FailedPageScrape",
    "HomepageFetchResult",
    "InformationGap",
    "PageSelectionDecision",
    "PageSelectionResult",
    "ProductServiceItem",
    "SelectedPage",
    "SitemapDiscoveryResult",
    "TargetAudienceInfo",
    "URLExtractionResult",
    "ValueProposition",
    "WebsiteCrawlResult",
    "WebsitePipelineResult",
    "WebsiteURLDiscoveryResult",
    "check_category_coverage",
    "classify_page_type",
    "crawl_selected_pages",
    "crawl_website_content",
    "discover_and_select_pages",
    "discover_sitemap_urls",
    "discover_website_urls",
    "extract_candidate_urls",
    "fetch_homepage",
    "generate_business_overview",
    "merge_candidate_urls",
    "select_pages_deterministic",
    "select_pages_with_llm",
    "should_trigger_sitemap_fallback",
]
