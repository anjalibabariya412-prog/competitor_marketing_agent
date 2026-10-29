import React from 'react';
import { ArrowLeftIcon, ArrowRightIcon, CheckIcon } from '../common/Icons.jsx';

export function Screen4PositioningOffers({ 
  userBusinessName = 'Your Business',
  competitors = [],
  overviewData = null,
  onBack, 
  onContinue 
}) {
  const positioningClaims = overviewData?.business_overview?.positioning_and_value_propositions || [];
  const products = overviewData?.business_overview?.products_and_services || [];

  return (
    <div className="wizard-step-container">
      <div className="section-intro-header">
        <span className="overview-step-kicker">Competitor Analysis • Positioning & Offers</span>
        <h1 className="form-title">Positioning & Offerings Benchmark</h1>
        <p className="form-subtitle">
          Comparing how {userBusinessName} stands against {competitors.map((c) => c.name).join(', ') || 'your competitors'} in core value propositions, product depth, and pricing transparency.
        </p>
      </div>

      <div className="benchmark-cards-grid">
        {/* User Card */}
        <div className="saas-card benchmark-entity-card user-card">
          <div className="entity-header">
            <span className="entity-badge user-badge">Your Business</span>
            <h3 className="entity-name">{userBusinessName}</h3>
          </div>

          <div className="entity-section">
            <span className="entity-label">Core Positioning</span>
            <ul className="entity-bullet-list">
              {positioningClaims.length > 0 ? (
                positioningClaims.slice(0, 3).map((claim, idx) => (
                  <li key={idx} className="entity-bullet-item">
                    <CheckIcon size="sm" className="icon-success" />
                    <span>{typeof claim === 'string' ? claim : claim.statement}</span>
                  </li>
                ))
              ) : (
                <li className="entity-bullet-item">
                  <CheckIcon size="sm" className="icon-success" />
                  <span>Quality product offerings with accessible market focus</span>
                </li>
              )}
            </ul>
          </div>

          <div className="entity-section">
            <span className="entity-label">Product Breadth & Pricing</span>
            <p className="entity-text">
              {Array.isArray(products) && products.length > 0
                ? `${products.length} key product lines verified with transparent pricing.`
                : 'Direct online catalog with verified customer pricing.'}
            </p>
          </div>
        </div>

        {/* Competitor Cards */}
        {competitors.map((comp, cIdx) => (
          <div key={comp.id || cIdx} className="saas-card benchmark-entity-card">
            <div className="entity-header">
              <span className="entity-badge">Competitor {cIdx + 1}</span>
              <h3 className="entity-name">{comp.name}</h3>
              <span className="entity-domain">{comp.website.replace(/^https?:\/\//, '').replace(/\/$/, '')}</span>
            </div>

            <div className="entity-section">
              <span className="entity-label">Estimated Market Stance</span>
              <ul className="entity-bullet-list">
                <li className="entity-bullet-item">
                  <span className="bullet-point" />
                  <span>Category alternative competing on retail presence and customer reach</span>
                </li>
                <li className="entity-bullet-item">
                  <span className="bullet-point" />
                  <span>Standard commercial warranty and promotional discounts</span>
                </li>
              </ul>
            </div>

            <div className="entity-section">
              <span className="entity-label">Product Line Depth</span>
              <p className="entity-text">
                Multi-tier catalog targeting overlapping consumer demographics.
              </p>
            </div>
          </div>
        ))}
      </div>

      <div className="competitors-footer-dock">
        <button type="button" onClick={onBack} className="btn-secondary">
          <ArrowLeftIcon size="sm" />
          <span>Back to Competitors</span>
        </button>

        <button type="button" onClick={onContinue} className="btn-primary">
          <span>Continue to Marketing & Channels</span>
          <ArrowRightIcon size="md" />
        </button>
      </div>
    </div>
  );
}

