import React from 'react';
import { adaptBusinessOverview } from '../../adapters/overviewAdapter.js';
import { 
  ArrowRightIcon, 
  ExternalLinkIcon, 
  GlobeIcon, 
  PackageIcon, 
  BriefcaseIcon, 
  ShoppingBagIcon, 
  UsersIcon,
  ShieldAlertIcon
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

/**
 * ViewSourceLink: lightweight, professional inline link for verifying factual claims.
 * Renders only when a valid, non-empty HTTP/HTTPS URL exists.
 */
function ViewSourceLink({ sourceUrl }) {
  if (!sourceUrl || typeof sourceUrl !== 'string' || !sourceUrl.startsWith('http')) {
    return null;
  }

  return (
    <a
      href={sourceUrl}
      target="_blank"
      rel="noopener noreferrer"
      className="view-source-link"
      title={`Verify source: ${sourceUrl}`}
    >
      <span>View source</span>
      <ExternalLinkIcon size="sm" />
    </a>
  );
}

export function Screen2Overview({ rawResponse, onBack, onContinue }) {
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
  } = data;

  return (
    <div className="overview-page-wrapper">
      {/* 1. Overview Hero Header Card */}
      <div className="saas-panel overview-hero-panel">
        <div className="hero-panel-top">
          <div className="hero-text-col">
            <span className="hero-step-kicker">STEP 2 OF 3 • BUSINESS OVERVIEW</span>
            <h1 className="hero-company-name">{businessName}</h1>
            {websiteUrl && (
              <div className="hero-meta-row">
                <a
                  href={websiteUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="meta-link-pill"
                >
                  <GlobeIcon size="sm" />
                  <span>{websiteUrl.replace(/^https?:\/\//, '').replace(/\/$/, '')}</span>
                  <ExternalLinkIcon size="sm" />
                </a>
              </div>
            )}
          </div>
        </div>

        {/* Business Summary Description */}
        {businessDescription && (
          <div className="hero-description-tray">
            <p className="hero-lead-paragraph">{businessDescription}</p>
          </div>
        )}
      </div>

      {/* 2. Structured Content Sections */}
      <div className="overview-sections-stack">
        {/* Section 1: What You Offer */}
        <section className="saas-panel section-panel">
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

        {/* Section 2: Who You Serve */}
        <section className="saas-panel section-panel">
          <div className="panel-title-bar">
            <div>
              <h2 className="panel-heading">Who You Serve</h2>
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
              <ViewSourceLink sourceUrl={audience?.provenance?.sourceUrl} />
            </div>
          </div>
        </section>

        {/* Section 3: How You Position Yourself */}
        <section className="saas-panel section-panel">
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
                  <span className="number-indicator" aria-hidden="true">{idx + 1}</span>
                  <div className="numbered-item-content">
                    <p className="numbered-item-text">{item.statement}</p>
                    <ViewSourceLink sourceUrl={item.provenance?.sourceUrl} />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="empty-panel-text">No distinct positioning claims identified.</p>
          )}
        </section>

        {/* Section 4: Key Findings */}
        <section className="saas-panel section-panel">
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
                  <span className="number-indicator" aria-hidden="true">{idx + 1}</span>
                  <div className="numbered-item-content">
                    <p className="numbered-item-text">{finding.statement}</p>
                    {finding.provenance?.hasConflict && (
                      <div className="conflict-notice-pill">
                        <ShieldAlertIcon size="sm" />
                        <span>Conflicting information detected on site</span>
                      </div>
                    )}
                    <ViewSourceLink sourceUrl={finding.provenance?.sourceUrl} />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="empty-panel-text">No additional findings reported.</p>
          )}
        </section>
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
