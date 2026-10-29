import React from 'react';
import { CheckIcon, SunIcon, MoonIcon } from './Icons.jsx';
import { WIZARD_STEPS } from '../../constants/steps.js';

export { WIZARD_STEPS };

export function TopBar({ currentStep, onStepClick, theme, onToggleTheme }) {
  const normalizedCurrentStep = Math.min(Math.max(currentStep || 1, 1), 3);
  const currentStepObj = WIZARD_STEPS.find((s) => s.id === normalizedCurrentStep) || WIZARD_STEPS[0];

  return (
    <header className="saas-top-bar">
      <div className="top-bar-container">
        {/* Left: Product Identity and 3-step progress navigation placed alongside */}
        <div className="top-bar-left-group">
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

          <div className="top-bar-divider" aria-hidden="true" />

          {/* 3-Step Wizard Navigation alongside Identity */}
          <nav className="stepper-container" aria-label="Workflow progress">
            <ol className="stepper-nodes-row">
              {WIZARD_STEPS.map((step, idx) => {
                const isCurrent = normalizedCurrentStep === step.id;
                const isPast = normalizedCurrentStep > step.id;
                const isClickable = currentStep > step.id && Boolean(onStepClick);

                return (
                  <li
                    key={step.id}
                    className={`wizard-node-item ${isCurrent ? 'active' : ''} ${isPast ? 'completed' : 'upcoming'} ${isClickable ? 'clickable' : ''}`}
                    aria-current={isCurrent ? 'step' : undefined}
                    onClick={() => {
                      if (isClickable) onStepClick(step.id);
                    }}
                    tabIndex={isClickable ? 0 : -1}
                    onKeyDown={(e) => {
                      if (isClickable && (e.key === 'Enter' || e.key === ' ')) {
                        e.preventDefault();
                        onStepClick(step.id);
                      }
                    }}
                  >
                    <span className="node-indicator">
                      {isPast ? <CheckIcon size="sm" /> : step.id}
                    </span>
                    <span className="node-title">{step.label}</span>
                    {idx < WIZARD_STEPS.length - 1 && (
                      <span className="node-connector-line" aria-hidden="true" />
                    )}
                  </li>
                );
              })}
            </ol>

            {/* Compact Mobile Stepper Pill */}
            <div className="mobile-stepper-pill" aria-hidden="true">
              <span className="mobile-step-counter">Step {normalizedCurrentStep} of {WIZARD_STEPS.length}</span>
              <span className="mobile-step-name">{currentStepObj.label}</span>
            </div>
          </nav>
        </div>

        {/* Right: Functional Theme Toggle */}
        <div className="top-bar-right-dock">
          <button
            type="button"
            className="theme-mode-button"
            onClick={onToggleTheme}
            aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
            title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
          >
            {theme === 'dark' ? <SunIcon size="sm" /> : <MoonIcon size="sm" />}
          </button>
        </div>
      </div>
    </header>
  );
}
