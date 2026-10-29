import React, { useState } from 'react';
import { 
  validateCompetitorList, 
  normalizeUrl 
} from '../../utils/validation.js';
import { discoverCompetitors } from '../../api/client.js';
import { 
  ArrowLeftIcon, 
  ArrowRightIcon, 
  PlusIcon, 
  TrashIcon, 
  SearchIcon, 
  AlertCircleIcon, 
  GlobeIcon 
} from '../common/Icons.jsx';

export function Screen3Competitors({ 
  userWebsite = '', 
  productOrService = '', 
  businessOverview = null,
  initialCompetitors = [], 
  initialMode = 'manual',
  onBack, 
  onContinue 
}) {
  const [mode, setMode] = useState(initialMode || 'manual');

  // Option A manual rows
  const [competitors, setCompetitors] = useState(
    initialCompetitors && initialCompetitors.length > 0
      ? initialCompetitors
      : [{ id: 'comp_1', name: '', website: '' }]
  );

  const [touched, setTouched] = useState({});

  // Option B discovery state
  const [isDiscovering, setIsDiscovering] = useState(false);
  const [discoveryError, setDiscoveryError] = useState(null);
  const [candidates, setCandidates] = useState([]);
  const [discoveryDone, setDiscoveryDone] = useState(false);

  const handleRowChange = (index, field, value) => {
    setCompetitors((prev) => {
      const next = [...prev];
      next[index] = { ...next[index], [field]: value };
      return next;
    });
  };

  const handleRowBlur = (index, field) => {
    setTouched((prev) => ({
      ...prev,
      [`${index}_${field}`]: true,
    }));
  };

  const addCompetitor = () => {
    if (competitors.length < 5) {
      setCompetitors((prev) => [
        ...prev,
        { id: `comp_${Date.now()}`, name: '', website: '' },
      ]);
    }
  };

  const removeCompetitor = (index) => {
    if (competitors.length > 1) {
      setCompetitors((prev) => prev.filter((_, i) => i !== index));
    }
  };

  // Option B discover action
  const handleDiscover = async () => {
    setIsDiscovering(true);
    setDiscoveryError(null);
    try {
      const results = await discoverCompetitors({
        businessWebsite: userWebsite,
        productOrService,
        businessName: businessOverview?.business_name,
      });
      setCandidates(results);
      setDiscoveryDone(true);
    } catch (err) {
      setDiscoveryError(err.message || 'Unable to discover competitors automatically.');
    } finally {
      setIsDiscovering(false);
    }
  };

  const removeCandidate = (id) => {
    const updated = candidates.filter((c) => c.id !== id);
    setCandidates(updated);
    if (updated.length === 0) {
      setMode('manual');
      setDiscoveryDone(false);
    }
  };

  const validation = validateCompetitorList(competitors, userWebsite);
  const isOptionAValid = validation.isValid;
  const isOptionBValid = discoveryDone && candidates.length > 0;
  const canProceed = mode === 'manual' ? isOptionAValid : isOptionBValid;

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!canProceed) return;

    if (mode === 'manual') {
      const normalizedList = competitors.map((c) => ({
        ...c,
        website: normalizeUrl(c.website).url,
      }));
      onContinue({ mode: 'manual', competitors: normalizedList });
    } else {
      const listFromCandidates = candidates.map((c) => ({
        id: c.id,
        name: c.name,
        website: normalizeUrl(c.website).url,
      }));
      onContinue({ mode: 'discover', competitors: listFromCandidates });
    }
  };

  return (
    <div className="competitors-container">
      <div className="section-intro-header">
        <span className="overview-step-kicker">Step 3 of 3 • Competitor Analysis</span>
        <h1 className="form-title">Select Your Competitors</h1>
        <p className="form-subtitle">
          Choose whether to specify competitors you already monitor or have our agent identify the top
          contenders based on your product offerings and positioning.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="competitors-selection-form">
        <div className="mode-selection-cards" role="radiogroup" aria-label="Competitor selection method">
          {/* Card A: Manual Entry */}
          <div
            className={`saas-card selection-card ${mode === 'manual' ? 'selected' : 'collapsed'}`}
            onClick={() => setMode('manual')}
            role="radio"
            aria-checked={mode === 'manual'}
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                setMode('manual');
              }
            }}
          >
            <div className="selection-card-header">
              <span className={`saas-radio-circle ${mode === 'manual' ? 'checked' : ''}`} />
              <div>
                <h3 className="selection-card-title">I know my competitors</h3>
                <p className="selection-card-desc">Enter 1 to 5 competitors to benchmark against.</p>
              </div>
            </div>

            {mode === 'manual' && (
              <div className="selection-card-body" onClick={(e) => e.stopPropagation()}>
                <div className="competitors-dynamic-list">
                  {competitors.map((comp, idx) => {
                    const rowErrors = validation.errors[idx] || {};
                    const nameErr = touched[`${idx}_name`] && rowErrors.name;
                    const webErr = touched[`${idx}_website`] && rowErrors.website;

                    return (
                      <div key={comp.id || idx} className="competitor-entry-box">
                        <div className="entry-header">
                          <span className="entry-badge">Competitor {idx + 1}</span>
                          {competitors.length > 1 && (
                            <button
                              type="button"
                              onClick={() => removeCompetitor(idx)}
                              className="btn-trash-remove"
                              title="Remove competitor"
                              aria-label={`Remove competitor ${idx + 1}`}
                            >
                              <TrashIcon size="sm" />
                              <span>Remove</span>
                            </button>
                          )}
                        </div>

                        <div className="entry-fields-row">
                          <div className={`entry-field-col ${nameErr ? 'has-error' : ''}`}>
                            <label htmlFor={`comp_name_${idx}`} className="saas-label">
                              Competitor name<span className="req-indicator">*</span>
                            </label>
                            <input
                              type="text"
                              id={`comp_name_${idx}`}
                              className="saas-input"
                              placeholder="e.g., Sugar Cosmetics"
                              value={comp.name}
                              onChange={(e) => handleRowChange(idx, 'name', e.target.value)}
                              onBlur={() => handleRowBlur(idx, 'name')}
                            />
                            {nameErr && (
                              <p className="saas-field-error" role="alert">
                                <AlertCircleIcon size="sm" />
                                <span>{nameErr}</span>
                              </p>
                            )}
                          </div>

                          <div className={`entry-field-col ${webErr ? 'has-error' : ''}`}>
                            <label htmlFor={`comp_web_${idx}`} className="saas-label">
                              Competitor website<span className="req-indicator">*</span>
                            </label>
                            <div className="input-with-icon">
                              <span className="input-icon-prefix" aria-hidden="true">
                                <GlobeIcon size="sm" />
                              </span>
                              <input
                                type="text"
                                id={`comp_web_${idx}`}
                                className="saas-input has-prefix"
                                placeholder="sugarcosmetics.com"
                                value={comp.website}
                                onChange={(e) => handleRowChange(idx, 'website', e.target.value)}
                                onBlur={() => handleRowBlur(idx, 'website')}
                              />
                            </div>
                            {webErr && (
                              <p className="saas-field-error" role="alert">
                                <AlertCircleIcon size="sm" />
                                <span>{webErr}</span>
                              </p>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>

                <div className="competitors-action-bar">
                  {competitors.length < 5 ? (
                    <button
                      type="button"
                      onClick={addCompetitor}
                      className="btn-secondary"
                    >
                      <PlusIcon size="sm" />
                      <span>Add Another Competitor</span>
                    </button>
                  ) : (
                    <span className="max-limit-caption">Maximum 5 competitors reached.</span>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Card B: Auto-Discovery */}
          <div
            className={`saas-card selection-card ${mode === 'discover' ? 'selected' : 'collapsed'}`}
            onClick={() => setMode('discover')}
            role="radio"
            aria-checked={mode === 'discover'}
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                setMode('discover');
              }
            }}
          >
            <div className="selection-card-header">
              <span className={`saas-radio-circle ${mode === 'discover' ? 'checked' : ''}`} />
              <div>
                <h3 className="selection-card-title">I don't know, find them for me</h3>
                <p className="selection-card-desc">We'll find up to 3 competitors and you'll confirm them before we analyse.</p>
              </div>
            </div>

            {mode === 'discover' && (
              <div className="selection-card-body" onClick={(e) => e.stopPropagation()}>
                {!discoveryDone ? (
                  <div className="discover-prompt-box">
                    <p className="discover-lead-text">
                      Our system will evaluate businesses offering similar products or services in your category.
                      You can review, confirm, or remove any discovered candidates.
                    </p>

                    {discoveryError && (
                      <p className="saas-field-error" role="alert">
                        <AlertCircleIcon size="sm" />
                        <span>{discoveryError}</span>
                      </p>
                    )}

                    <button
                      type="button"
                      onClick={handleDiscover}
                      disabled={isDiscovering}
                      className="btn-primary"
                    >
                      <SearchIcon size="md" />
                      <span>{isDiscovering ? 'Finding competitors...' : 'Find Competitors'}</span>
                    </button>
                  </div>
                ) : (
                  <div className="candidates-review-box">
                    <div className="candidates-bar-title">
                      <span className="bar-heading">Review Discovered Competitors</span>
                      <span className="bar-subheading">Remove any competitors you do not wish to benchmark against.</span>
                    </div>

                    <div className="candidates-cards-stack">
                      {candidates.map((cand) => (
                        <div key={cand.id} className="candidate-item-row">
                          <div className="candidate-info-col">
                            <div className="candidate-title-line">
                              <h4 className="candidate-business-name">{cand.name}</h4>
                              <a
                                href={cand.website}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="candidate-domain-link"
                              >
                                <span>{cand.website.replace(/^https?:\/\//, '').replace(/\/$/, '')}</span>
                              </a>
                            </div>
                            <p className="candidate-reasoning">{cand.reason}</p>
                          </div>

                          <button
                            type="button"
                            onClick={() => removeCandidate(cand.id)}
                            className="btn-candidate-remove"
                            title="Remove candidate"
                            aria-label={`Remove ${cand.name}`}
                          >
                            <TrashIcon size="sm" />
                          </button>
                        </div>
                      ))}
                    </div>

                    <div className="candidates-status-summary">
                      <span>{candidates.length} candidate competitors confirmed.</span>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Action Footer */}
        <div className="competitors-footer-dock">
          <button type="button" onClick={onBack} className="btn-secondary">
            <ArrowLeftIcon size="sm" />
            <span>Back to Overview</span>
          </button>

          <button
            type="submit"
            className="btn-primary"
            disabled={!canProceed}
          >
            <span>Continue to Positioning</span>
            <ArrowRightIcon size="md" />
          </button>
        </div>
      </form>
    </div>
  );
}
