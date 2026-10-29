import React from 'react';
import { ArrowLeftIcon, ArrowRightIcon } from '../common/Icons.jsx';

export function Screen5MarketingChannels({ 
  userBusinessName = 'Your Business',
  competitors = [],
  onBack, 
  onContinue 
}) {
  return (
    <div className="wizard-step-container">
      <div className="section-intro-header">
        <span className="overview-step-kicker">Competitor Analysis • Marketing & Channels</span>
        <h1 className="form-title">Marketing Channels & Visible Campaigns</h1>
        <p className="form-subtitle">
          Evaluating communication themes, active digital channels, and customer acquisition strategies across the competitive field.
        </p>
      </div>

      <div className="channels-cards-stack">
        <div className="saas-card channel-overview-card">
          <h2 className="channel-section-title">Observable Marketing Presence</h2>
          <p className="channel-section-desc">
            Comparative scan of visible digital channels, campaign angles, and content themes.
          </p>

          <div className="channel-matrix-rows">
            <div className="channel-row">
              <span className="channel-label">Channel Footprint</span>
              <div className="channel-comparison-items">
                <div className="channel-item you-item">
                  <strong>{userBusinessName}:</strong>
                  <span>Website storefront, organic search presence, direct checkout</span>
                </div>
                {competitors.map((comp, idx) => (
                  <div key={comp.id || idx} className="channel-item">
                    <strong>{comp.name}:</strong>
                    <span>Website catalog, retailer networks, search visibility</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="channel-row">
              <span className="channel-label">Tone & Messaging Hooks</span>
              <div className="channel-comparison-items">
                <div className="channel-item you-item">
                  <strong>{userBusinessName}:</strong>
                  <span>Direct, value-oriented, accessible consumer appeal</span>
                </div>
                {competitors.map((comp, idx) => (
                  <div key={comp.id || idx} className="channel-item">
                    <strong>{comp.name}:</strong>
                    <span>Broad lifestyle appeal, periodic seasonal discounts</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="channel-row">
              <span className="channel-label">Visible Strengths & Gaps</span>
              <div className="channel-comparison-items">
                <div className="channel-item you-item">
                  <strong>Opportunity:</strong>
                  <span>Direct-to-consumer agility and faster product iteration</span>
                </div>
                {competitors.map((comp, idx) => (
                  <div key={comp.id || idx} className="channel-item">
                    <strong>Market Vulnerability:</strong>
                    <span>High promotional dependence and fragmented product lines</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="competitors-footer-dock">
        <button type="button" onClick={onBack} className="btn-secondary">
          <ArrowLeftIcon size="sm" />
          <span>Back to Positioning</span>
        </button>

        <button type="button" onClick={onContinue} className="btn-primary">
          <span>Generate Final Report</span>
          <ArrowRightIcon size="md" />
        </button>
      </div>
    </div>
  );
}
