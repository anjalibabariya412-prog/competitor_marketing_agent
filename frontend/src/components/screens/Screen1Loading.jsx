import React from 'react';
import { GlobeIcon, AlertCircleIcon, RefreshIcon } from '../common/Icons.jsx';

export function Screen1Loading({ website, onCancel, error, onRetry }) {
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
            <div className="loading-visual-container">
              <div className="pulse-radar-loader" aria-hidden="true" />
            </div>

            {website && (
              <div className="loading-site-pill">
                <GlobeIcon size="sm" />
                <span>{website.replace(/^https?:\/\//, '').replace(/\/$/, '')}</span>
              </div>
            )}

            <h2 className="loading-box-title">Generating Business Overview</h2>
            <p className="loading-box-desc">
              Reviewing website pages and extracting offerings, value propositions, and customer profiles.
              This typically takes around 25–35 seconds.
            </p>

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
