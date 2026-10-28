"""Enum definitions for web crawling, social sources, and competitive findings."""

from enum import Enum


class PageType(str, Enum):
    """Classification of crawled web pages."""

    HOME = "home"
    ABOUT = "about"
    PRODUCT = "product"
    SERVICE = "service"
    PRICING = "pricing"
    OFFER = "offer"
    FAQ = "faq"
    POLICY = "policy"
    CONTACT = "contact"
    BLOG = "blog"
    OTHER = "other"
    UNKNOWN = "unknown"


class Platform(str, Enum):
    """Supported online platforms and social networks."""

    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    LINKEDIN = "linkedin"
    WEBSITE = "website"


class SocialSource(str, Enum):
    """Discovery source mechanism for social media profile links."""

    USER_PROVIDED = "user_provided"
    JSON_LD = "json_ld"
    PAGE_TEXT = "page_text"


class FindingType(str, Enum):
    """Analytical categories for competitive findings and factual statements."""

    POSITIONING = "positioning"
    TARGET_AUDIENCE = "target_audience"
    PRODUCT_SERVICE = "product_service"
    PRICING = "pricing"
    OFFER = "offer"
    MESSAGING = "messaging"
    MARKETING_CHANNEL = "marketing_channel"
    VISIBLE_ACTIVITY = "visible_activity"
    STRENGTH = "strength"
    WEAKNESS = "weakness"
    OTHER = "other"
