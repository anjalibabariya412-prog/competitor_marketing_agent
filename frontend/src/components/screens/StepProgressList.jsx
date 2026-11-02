import React from 'react';
import { CheckIcon } from '../common/Icons.jsx';

const STAGES = [
  {
    id: 'visiting_website',
    label: 'Visiting website',
    getRunningDetail: (meta) => {
      if (meta?.website_url) {
        const clean = meta.website_url.replace(/^https?:\/\//, '').replace(/\/$/, '');
        return `Connecting to ${clean} and discovering pages...`;
      }
      return 'Connecting to site and discovering pages...';
    },
    getDoneDetail: () => 'Website connected and pages discovered',
  },
  {
    id: 'reading_pages',
    label: 'Reading website pages',
    getRunningDetail: (meta) => {
      if (meta?.page_count) {
        return `Reading and extracting content from ${meta.page_count} selected page${meta.page_count === 1 ? '' : 's'}...`;
      }
      if (meta?.found_pages) {
        return `Discovered ${meta.found_pages} candidate pages, reading selected pages...`;
      }
      return 'Extracting content from selected pages...';
    },
    getDoneDetail: (meta) => {
      if (meta?.page_count) {
        return `Scraped ${meta.page_count} page${meta.page_count === 1 ? '' : 's'}`;
      }
      return 'Scraped page content';
    },
  },
  {
    id: 'extracting_info',
    label: 'Extracting offerings & positioning',
    getRunningDetail: (meta) => {
      if (meta?.total_chars) {
        const kChars = Math.round(meta.total_chars / 1000);
        return `Analyzing ~${kChars}k characters across ${meta.page_count || 1} pages...`;
      }
      return 'Analyzing core products, services, and positioning from scraped content...';
    },
    getDoneDetail: () => 'Key offerings and value proposition extracted',
  },
  {
    id: 'identifying_audience',
    label: 'Identifying target customers',
    getRunningDetail: (meta) => {
      if (meta?.audience_context === 'user_provided') {
        return 'Evaluating user-provided target audience against site evidence...';
      }
      return 'Inferring customer profiles, segments, and market focus...';
    },
    getDoneDetail: () => 'Target customer profiles identified',
  },
  {
    id: 'writing_overview',
    label: 'Generating Business Overview',
    getRunningDetail: () => 'Synthesizing comprehensive structured overview with Gemini...',
    getDoneDetail: () => 'Business Overview generation complete',
  },
];

export function StepProgressList({ currentStage, liveMeta = {}, isDone = false }) {
  const currentStageIndex = STAGES.findIndex((s) => s.id === currentStage);
  const activeIdx = currentStageIndex === -1 ? 0 : currentStageIndex;

  return (
    <div className="progress-stepper-container" role="status" aria-live="polite">
      <ul className="progress-stepper-list">
        {STAGES.map((stage, idx) => {
          let status = 'pending';
          if (isDone) {
            status = 'done';
          } else if (idx < activeIdx) {
            status = 'done';
          } else if (idx === activeIdx) {
            status = 'running';
          }

          let detailText = null;
          if (status === 'running') {
            detailText = stage.getRunningDetail(liveMeta);
          } else if (status === 'done') {
            detailText = stage.getDoneDetail(liveMeta);
          }

          return (
            <li
              key={stage.id}
              className={`progress-step-item progress-step-${status}`}
              aria-current={status === 'running' ? 'step' : undefined}
            >
              <div className="progress-step-indicator-wrapper">
                <div className={`step-icon-badge step-badge-${status}`}>
                  {status === 'done' ? (
                    <CheckIcon size="sm" className="step-check-icon" />
                  ) : status === 'running' ? (
                    <span className="step-spinner" aria-hidden="true" />
                  ) : (
                    <span className="step-dot" aria-hidden="true" />
                  )}
                </div>
                {idx < STAGES.length - 1 && (
                  <div
                    className={`progress-step-line ${
                      isDone || idx < activeIdx ? 'progress-step-line-done' : ''
                    }`}
                    aria-hidden="true"
                  />
                )}
              </div>

              <div className="progress-step-content">
                <div className="progress-step-label-row">
                  <span className={`progress-step-label progress-step-label-${status}`}>
                    {stage.label}
                  </span>
                  {status === 'running' && (
                    <span className="step-badge-tag-running">In progress</span>
                  )}
                </div>
                {detailText && (
                  <p className={`progress-step-detail progress-step-detail-${status}`}>
                    {detailText}
                  </p>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

