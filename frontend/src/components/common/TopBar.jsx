import React from 'react';
import { WIZARD_STEPS } from '../../constants/steps.js';

export { WIZARD_STEPS };

export function TopBar({ currentStep, onStepClick }) {
  return (
    <header className="saas-top-bar">
      <div className="top-bar-container">
        {/* Center: Brand / Agent Identity Tag */}
        <div className="top-bar-identity">
          <div className="brand-logo-emblem" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path d="M12 2L2 7L12 12L22 7L12 2Z" fill="white" fillOpacity="0.9" />
              <path d="M2 17L12 22L22 17" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M2 12L12 17L22 12" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <span className="brand-product-title">Competitor Marketing Agent</span>
        </div>
      </div>
    </header>
  );
}
