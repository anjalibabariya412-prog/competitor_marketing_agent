import React, { useState, useMemo } from 'react';
import { adaptBusinessOverview } from '../../adapters/overviewAdapter.js';
import {
  ArrowRightIcon,
  CheckIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  ExternalLinkIcon,
  GlobeIcon,
  PackageIcon,
  BriefcaseIcon,
  ShoppingBagIcon,
  UsersIcon,
  ShieldAlertIcon,
} from '../common/Icons.jsx';

/**
 * Resolves a neutral, category-appropriate icon for products or services.
 * Respects strict visual rule: simple, consistent category icons without company-specific logos.
 */
function getCategoryIcon(item = {}) {
  const text = `${item.name || ''} ${item.description || ''}`.toLowerCase();
  if (/service|consult|advis|agenc|manag|support|solut|train|audit|hire|partner|platform|software|api/i.test(text)) {
    return BriefcaseIcon;
  }
  if (/shop|store|cart|retail|cosmetic|beauty|apparel|cloth|skincare|serum|cream|powder|makeup/i.test(text)) {
    return ShoppingBagIcon;
  }
  return PackageIcon;
}

export function Screen2Overview({ rawResponse, onBack, onContinue }) {
  const [sourcesExpanded, setSourcesExpanded] = useState(false);
  const data = adaptBusinessOverview(rawResponse);

  if (!data) {
    return (
      <div className="empty-overview-card saas-panel">
        <h3>Overview Unavailable</h3>
        <p>No valid business overview data was found in the response.</p>
        <button type="button" onClick={onBack} className="btn-secondary-action">
          Return to Form
        </button>
      </div>
    );
  }

  const {
    businessName,
    websiteUrl,
    businessDescription,
    products,
    audience,
    positioning,
    findings,
    sourcesUsed,
    pageCount,
  } = data;

  const keySources = useMemo(() => {
    const urlSet = new Set();

    const addUrl = (url) => {
      if (url && typeof url === 'string') {
        const trimmed = url.trim();
        if (trimmed.startsWith('http://') || trimmed.startsWith('https://')) {
          urlSet.add(trimmed);
        }
      }
    };

    // 1. Backend-compiled sources_used
    if (Array.isArray(sourcesUsed)) {
      sourcesUsed.forEach(addUrl);
    }

    // 2. Audience source
    addUrl(audience?.provenance?.sourceUrl);

    // 3. Positioning value propositions sources
    if (Array.isArray(positioning)) {
      positioning.forEach((item) => {
        addUrl(item.provenance?.sourceUrl);
      });
    }

    // 4. Key findings sources
    if (Array.isArray(findings)) {
      findings.forEach((finding) => {
        addUrl(finding.provenance?.sourceUrl);
      });
    }

    // 5. Products / offerings sources
    if (Array.isArray(products)) {
      products.forEach((prod) => {
        addUrl(prod.provenance?.sourceUrl);
      });
    }

    // 6. Direct fallback from rawResponse if present
    const rawOverview = rawResponse?.business_overview;
    if (rawOverview) {
      if (Array.isArray(rawOverview.sources_used)) {
        rawOverview.sources_used.forEach(addUrl);
      }
      if (Array.isArray(rawOverview.key_findings)) {
        rawOverview.key_findings.forEach((f) => {
          addUrl(f.source_url);
          if (Array.isArray(f.source_urls)) f.source_urls.forEach(addUrl);
        });
      }
      if (Array.isArray(rawOverview.positioning_and_value_propositions)) {
        rawOverview.positioning_and_value_propositions.forEach((vp) => {
          addUrl(vp.source_url);
          if (Array.isArray(vp.source_urls)) vp.source_urls.forEach(addUrl);
        });
      }
      if (Array.isArray(rawOverview.products_and_services)) {
        rawOverview.products_and_services.forEach((p) => {
          addUrl(p.source_url);
          if (Array.isArray(p.source_urls)) p.source_urls.forEach(addUrl);
        });
      }
      if (rawOverview.target_audience) {
        addUrl(rawOverview.target_audience.source_url);
      }
    }

    return Array.from(urlSet);
  }, [sourcesUsed, audience, positioning, findings, products, rawResponse]);

  const actualPageCount =
    (typeof pageCount === 'number' && pageCount > 0)
      ? pageCount
      : (keySources.length > 0 ? keySources.length : (rawResponse?.total_pages_analyzed || 0));

  return (
    <div className="overview-page-wrapper">
      {/* 1. Distinct Introduction Container */}
      <div className="overview-intro-panel">
        <div className="overview-intro-top-row">
          <div className="overview-intro-icon-wrap" aria-hidden="true">
            <GlobeIcon size="sm" />
          </div>
          <div className="overview-intro-headings">
            <h1 className="overview-intro-title">Your Business Overview</h1>
            <p className="overview-intro-subtitle">AI-generated summary based on your website</p>
          </div>
        </div>

        <p className="overview-intro-description">
          We reviewed your website to understand your business, products, brand positioning, and visible offers. The overview below summarizes the information found across the pages we reviewed.
        </p>

        {/* Website Review Summary (2 Cards) */}
        <div className="website-review-summary-row">
          <div className="review-summary-item">
            <div className="summary-item-icon-wrap" aria-hidden="true">
              <GlobeIcon size="sm" />
            </div>
            <div className="summary-item-text-group">
              <span className="summary-item-label">Website reviewed</span>
              <span className="summary-item-subtext">
                {actualPageCount > 0
                  ? `${actualPageCount} ${actualPageCount === 1 ? 'page' : 'pages'} reviewed`
                  : 'Website pages reviewed'}
              </span>
            </div>
          </div>

          <div className="review-summary-item">
            <div className="summary-item-icon-wrap" aria-hidden="true">
              <CheckIcon size="sm" />
            </div>
            <div className="summary-item-text-group">
              <span className="summary-item-label">Evidence-based overview</span>
              <span className="summary-item-subtext">Based on reviewed website content</span>
            </div>
          </div>
        </div>
      </div>

      {/* 2. Main Continuous Report Container */}
      <div className="saas-panel overview-report-sheet">
        {/* Business Overview Header */}
        <header className="overview-report-header">
          <div className="business-profile-title-row">
            <div className="business-static-logo" aria-hidden="true">
              <BriefcaseIcon size="md" />
            </div>
            <h2 className="hero-company-name">{businessName}</h2>
          </div>
        </header>

        {/* Section 1: About Your Business */}
        {businessDescription && (
          <section className="overview-report-section">
            <div className="panel-title-bar">
              <div>
                <h2 className="panel-heading">About Your Business</h2>
              </div>
            </div>
            <p className="about-business-text">{businessDescription}</p>
          </section>
        )}

        {/* Section 2: What You Offer */}
        <section className="overview-report-section">
          <div className="panel-title-bar">
            <div>
              <h2 className="panel-heading">What You Offer</h2>
              <p className="panel-subheading">Core products and primary service lines identified on your website.</p>
            </div>
          </div>

          {products && products.length > 0 ? (
            <div className="offerings-cards-grid">
              {products.map((prod, idx) => {
                const CategoryIcon = getCategoryIcon(prod);
                return (
                  <div key={prod.id || idx} className="offering-box">
                    <div className="offering-header-row">
                      <div className="offering-icon-wrap" aria-hidden="true">
                        <CategoryIcon size="md" className="offering-cat-icon" />
                      </div>
                      <h3 className="offering-title">{prod.name}</h3>
                    </div>

                    {prod.description && (
                      <p className="offering-body">{prod.description}</p>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="empty-panel-text">Primary offerings are detailed in the executive overview above.</p>
          )}
        </section>

        {/* Section 3: Key Findings */}
        <section className="overview-report-section">
          <div className="panel-title-bar">
            <div>
              <h2 className="panel-heading">Key Findings</h2>
              <p className="panel-subheading">Significant observations about the business's products, offers, audience, and messaging.</p>
            </div>
          </div>

          {findings && findings.length > 0 ? (
            <div className="numbered-items-stack findings-stack">
              {findings.map((finding, idx) => (
                <div key={finding.id || idx} className="numbered-item-row finding-row">
                  <span className="bullet-indicator" aria-hidden="true" />
                  <div className="numbered-item-content">
                    <p className="numbered-item-text">{finding.statement}</p>
                    {finding.provenance?.hasConflict && (
                      <div className="conflict-notice-pill">
                        <ShieldAlertIcon size="sm" />
                        <span>Conflicting information detected on site</span>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="empty-panel-text">No additional findings reported.</p>
          )}
        </section>

        {/* Section 4: How You Position Yourself */}
        <section className="overview-report-section">
          <div className="panel-title-bar">
            <div>
              <h2 className="panel-heading">How You Position Yourself</h2>
              <p className="panel-subheading">Key positioning claims, value propositions, and market differentiators.</p>
            </div>
          </div>

          {positioning && positioning.length > 0 ? (
            <div className="numbered-items-stack">
              {positioning.map((item, idx) => (
                <div key={item.id || idx} className="numbered-item-row">
                  <span className="bullet-indicator" aria-hidden="true" />
                  <div className="numbered-item-content">
                    <p className="numbered-item-text">{item.statement}</p>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="empty-panel-text">No distinct positioning claims identified.</p>
          )}
        </section>

        {/* Section 5: Target Audience */}
        <section className="overview-report-section">
          <div className="panel-title-bar">
            <div>
              <h2 className="panel-heading">Target Audience</h2>
              <p className="panel-subheading">Target customer segments, demographics, and buyer personas.</p>
            </div>
          </div>

          <div className="audience-info-card">
            <div className="audience-icon-badge" aria-hidden="true">
              <UsersIcon size="md" />
            </div>
            <div className="audience-content-wrap">
              <p className="audience-lead-text">
                {audience?.description || 'Target customer profile derived from your business offerings and website messaging.'}
              </p>
              {audience?.provenance?.hasConflict && (
                <div className="conflict-notice-pill">
                  <ShieldAlertIcon size="sm" />
                  <span>Conflicting audience information detected on site</span>
                </div>
              )}
            </div>
          </div>
        </section>

        {/* 6. Collapsible Key Sources Row */}
        {keySources.length > 0 && (
          <div className="key-sources-collapsible">
            <button
              type="button"
              className="key-sources-toggle"
              onClick={() => setSourcesExpanded(!sourcesExpanded)}
              aria-expanded={sourcesExpanded}
            >
              <span className="key-sources-title">Key Sources</span>
              {sourcesExpanded ? (
                <ChevronUpIcon size="sm" className="key-sources-chevron" />
              ) : (
                <ChevronDownIcon size="sm" className="key-sources-chevron" />
              )}
            </button>

            {sourcesExpanded && (
              <div className="key-sources-links-list">
                {keySources.map((url) => (
                  <a
                    key={url}
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="key-source-link-item"
                  >
                    <span className="key-source-url-text">{url}</span>
                    <ExternalLinkIcon size="sm" className="key-source-ext-icon" />
                  </a>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* 3. Sticky Bottom Action Dock */}
      <div className="overview-bottom-action-dock">
        <button type="button" onClick={onContinue} className="btn-primary-action">
          <span>Continue</span>
          <ArrowRightIcon size="md" />
        </button>
      </div>
    </div>
  );
}
