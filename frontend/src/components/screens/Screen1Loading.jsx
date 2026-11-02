import React from 'react';
import { GlobeIcon, AlertCircleIcon, RefreshIcon } from '../common/Icons.jsx';
import { StepProgressList } from './StepProgressList.jsx';

function AnalysisBannerIllustration() {
  return (
    <svg
      className="progress-banner-svg"
      viewBox="0 0 100 80"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      {/* Browser Window Card */}
      <rect x="6" y="10" width="68" height="52" rx="6" fill="#FFFFFF" stroke="#D1E0FF" strokeWidth="1.5" />

      {/* Browser Header Bar */}
      <path d="M6 16C6 12.6863 8.68629 10 12 10H68C71.3137 10 74 12.6863 74 16V22H6V16Z" fill="#EEF4FF" />
      <line x1="6" y1="22" x2="74" y2="22" stroke="#D1E0FF" strokeWidth="1" />

      {/* Browser Dots */}
      <circle cx="12" cy="16" r="2" fill="#93C5FD" />
      <circle cx="18" cy="16" r="2" fill="#93C5FD" />
      <circle cx="24" cy="16" r="2" fill="#93C5FD" />

      {/* Browser URL pill */}
      <rect x="31" y="13" width="37" height="6" rx="3" fill="#FFFFFF" />

      {/* Analytics Bars inside Browser */}
      <rect x="14" y="44" width="6" height="12" rx="1.5" fill="#BFDBFE" />
      <rect x="23" y="38" width="6" height="18" rx="1.5" fill="#60A5FA" />
      <rect x="32" y="30" width="6" height="26" rx="1.5" fill="#315EFB" />

      {/* Content lines inside Browser */}
      <rect x="44" y="32" width="24" height="3" rx="1.5" fill="#EEF4FF" />
      <rect x="44" y="39" width="18" height="3" rx="1.5" fill="#EEF4FF" />
      <rect x="44" y="46" width="22" height="3" rx="1.5" fill="#EEF4FF" />

      {/* Search / Magnifying Glass overlay on the right */}
      <circle cx="68" cy="46" r="16" fill="#EEF4FF" fillOpacity="0.85" stroke="#315EFB" strokeWidth="2.5" />

      {/* Inside Lens: Analytics Trend */}
      <path d="M60 48L65 43L70 46L76 39" stroke="#315EFB" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="76" cy="39" r="1.5" fill="#315EFB" />

      {/* Magnifying Glass Handle */}
      <path d="M80 58L93 71" stroke="#1D4ED8" strokeWidth="4" strokeLinecap="round" />

      {/* Sparkle Accent */}
      <path d="M85 14C85 17.5 87.5 20 91 20C87.5 20 85 22.5 85 26C85 22.5 82.5 20 79 20C82.5 20 85 17.5 85 14Z" fill="#315EFB" />
      <circle cx="93" cy="28" r="1.5" fill="#93C5FD" />
    </svg>
  );
}

export function Screen1Loading({
  website,
  onCancel,
  error,
  onRetry,
  currentStage = null,
  liveMeta = {},
  isDone = false,
  fallbackMode = false,
}) {
  const showStepper = Boolean(currentStage && !fallbackMode);

  return (
    <div className="single-form-layout">
      <div className="form-card-wrapper">
        {error ? (
          <div className="loading-content-box error-state-box" role="alert">
            <div className="error-icon-circle">
              <AlertCircleIcon size="lg" />
            </div>
            <h2 className="loading-box-title">Generation Incomplete</h2>
            <p className="loading-box-desc">
              {error || 'Unable to complete the business overview. Please verify your website address and backend connection.'}
            </p>
            <div className="error-action-row">
              <button type="button" onClick={onRetry} className="btn-primary-cta">
                <RefreshIcon size="md" />
                <span>Retry Generation</span>
              </button>
              <button type="button" onClick={onCancel} className="btn-secondary-link">
                <span>Edit Business Details</span>
              </button>
            </div>
          </div>
        ) : (
          <div className="loading-content-box" aria-live="polite">
            {/* Informational Banner */}
            <div className="progress-info-banner">
              <div className="progress-banner-illustration" aria-hidden="true">
                <AnalysisBannerIllustration />
              </div>
              <div className="progress-banner-divider" aria-hidden="true" />
              <div className="progress-banner-text">
                <h3 className="progress-banner-title">Analyzing your business website</h3>
                <p className="progress-banner-desc">
                  Our agent is now exploring your website to understand your business, products or services, positioning, and key information. Next, it will use these insights to compare your business with competitors and generate a detailed competitor analysis.
                </p>
              </div>
            </div>

            {!showStepper && (
              <div className="loading-visual-container">
                <div className="pulse-radar-loader" aria-hidden="true" />
              </div>
            )}

            {website && (
              <div className="loading-site-pill">
                <GlobeIcon size="sm" />
                <span>{website.replace(/^https?:\/\//, '').replace(/\/$/, '')}</span>
              </div>
            )}

            <h2 className="loading-box-title">Generating Business Overview</h2>

            {showStepper ? (
              <StepProgressList
                currentStage={currentStage}
                liveMeta={liveMeta}
                isDone={isDone}
              />
            ) : (
              <p className="loading-box-desc">
                Reviewing website pages and extracting offerings, value propositions, and customer profiles.
                This typically takes around 25–35 seconds.
              </p>
            )}

            <div className="loading-meta-dock">
              <button type="button" onClick={onCancel} className="btn-cancel-flat">
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
