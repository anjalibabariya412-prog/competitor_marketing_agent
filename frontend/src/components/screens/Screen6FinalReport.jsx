import React, { useState, useEffect } from 'react';
import { ArrowLeftIcon, CheckIcon } from '../common/Icons.jsx';

const REPORT_SECTIONS = [
  { id: 'sec_1', title: 'Overview', desc: 'Executive market summary and competitive landscape.' },
  { id: 'sec_2', title: 'Competitor Positioning', desc: 'Market stance, target audience, and core differentiators.' },
  { id: 'sec_3', title: 'Offers', desc: 'Products, services, public pricing visibility, and guarantees.' },
  { id: 'sec_4', title: 'Messaging', desc: 'Tone of voice, key hooks, slogans, and recurring themes.' },
  { id: 'sec_5', title: 'Marketing Channels and Visible Activity', desc: 'Observed channels, campaign footprint, and content focus.' },
  { id: 'sec_6', title: 'Marketing Gaps and Competitive Opportunities', desc: 'Identified market voids, competitor vulnerabilities, and user advantages.' },
  { id: 'sec_7', title: 'Competitor Comparison', desc: 'Side-by-side benchmark matrix against your business.' },
];

const COMPARISON_DIMENSIONS = [
  { dimension: 'Market Positioning', you: 'Accessible Quality / Value Stance', compDefault: 'Broad Commercial or Specialty Niche' },
  { dimension: 'Target Audience', you: 'Value-Conscious Everyday Buyers', compDefault: 'Mass Retail & Category Shoppers' },
  { dimension: 'Primary Differentiator', you: 'Direct-to-Consumer Agility & Price Payoff', compDefault: 'Brand Heritage or Retail Distribution' },
  { dimension: 'Product & Pricing Visibility', you: 'Publicly Displayed Online Pricing', compDefault: 'Catalog Pricing / Promotional Bundles' },
  { dimension: 'Visible Marketing Channels', you: 'E-commerce Website & Search Discovery', compDefault: 'Storefronts, Search & Social Campaigns' },
  { dimension: 'Key Strategic Gap', you: 'Opportunity to capture underserved segments', compDefault: 'Heavier overhead and slower iteration' },
];

export function Screen6FinalReport({ 
  userBusinessName = 'Your Business',
  competitors = [], 
  onBack 
}) {
  const [analyzing, setAnalyzing] = useState(true);
  const [activeCompIndex, setActiveCompIndex] = useState(1);

  const totalCompetitors = Math.max(competitors.length, 1);

  useEffect(() => {
    const timer = setInterval(() => {
      setActiveCompIndex((prev) => {
        if (prev < totalCompetitors) {
          return prev + 1;
        } else {
          setAnalyzing(false);
          clearInterval(timer);
          return prev;
        }
      });
    }, 1000);

    return () => clearInterval(timer);
  }, [totalCompetitors]);

  return (
    <div className="report-container">
      {/* Report Header */}
      <div className="saas-card report-hero-card">
        <div className="report-hero-top">
          <div>
            <span className="overview-step-kicker">Competitor Analysis • Final Report</span>
            <h1 className="overview-main-heading">Competitor Analysis Report</h1>
            <p className="report-subtitle">
              Comprehensive analysis benchmarking {userBusinessName} against {competitors.map((c) => c.name).join(', ') || 'selected competitors'}.
            </p>
          </div>

          <button type="button" onClick={onBack} className="btn-secondary">
            <ArrowLeftIcon size="sm" />
            <span>Back to Channels</span>
          </button>
        </div>
      </div>

      {/* Progress banner during initial report synthesis */}
      {analyzing ? (
        <div className="saas-card report-progress-box" role="status" aria-live="polite">
          <div className="progress-flex">
            <div className="subtle-spinner" aria-hidden="true" />
            <div>
              <h3 className="progress-title">Analysing competitor {activeCompIndex} of {totalCompetitors}</h3>
              <p className="progress-sub">
                Benchmarking positioning, pricing transparency, and marketing footprint for{' '}
                <strong>{competitors[activeCompIndex - 1]?.name || 'competitor'}</strong>...
              </p>
            </div>
          </div>
          <div className="progress-bar-track">
            <div
              className="progress-bar-fill"
              style={{ width: `${(activeCompIndex / totalCompetitors) * 100}%` }}
            />
          </div>
        </div>
      ) : (
        <div className="report-ready-banner">
          <CheckIcon size="sm" />
          <span>Analysis complete for all {totalCompetitors} competitors. All 7 sections rendered below.</span>
        </div>
      )}

      {/* 7 Report Sections */}
      <div className="report-sections-list">
        {REPORT_SECTIONS.map((sec, idx) => (
          <section key={sec.id} className="saas-card report-section-card">
            <div className="report-section-header">
              <span className="report-sec-index">0{idx + 1}</span>
              <div>
                <h2 className="report-sec-heading">{sec.title}</h2>
                <p className="report-sec-caption">{sec.desc}</p>
              </div>
            </div>

            {/* Section 7 Comparison Table */}
            {sec.id === 'sec_7' ? (
              <div className="comparison-matrix-wrapper">
                <table className="comparison-matrix-table">
                  <thead>
                    <tr>
                      <th className="dim-column">Comparison Criteria</th>
                      <th className="you-column">
                        <span className="you-tag">You</span>
                        <span className="entity-title-bold">{userBusinessName}</span>
                      </th>
                      {competitors.map((comp, cIdx) => (
                        <th key={comp.id || cIdx} className="comp-column">
                          <span className="comp-tag">Competitor {cIdx + 1}</span>
                          <span className="entity-title-bold">{comp.name}</span>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {COMPARISON_DIMENSIONS.map((row, rIdx) => (
                      <tr key={rIdx}>
                        <td className="dim-cell">{row.dimension}</td>
                        <td className="you-cell">{row.you}</td>
                        {competitors.map((comp, cIdx) => (
                          <td key={cIdx} className="comp-cell">
                            {row.compDefault}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="report-section-body">
                <p className="report-placeholder-text">
                  Evidence-grounded observations and synthesis for {sec.title.toLowerCase()} across all monitored competitors.
                </p>
              </div>
            )}
          </section>
        ))}
      </div>

      {/* Report Footer Bar */}
      <div className="overview-sticky-bottom-bar">
        <button type="button" onClick={onBack} className="btn-secondary">
          <ArrowLeftIcon size="sm" />
          <span>Back to Channels</span>
        </button>
      </div>
    </div>
  );
}

