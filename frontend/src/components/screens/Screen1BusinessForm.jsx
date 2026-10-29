import React, { useState } from 'react';
import { normalizeUrl } from '../../utils/validation.js';
import { GlobeIcon, AlertCircleIcon, ArrowRightIcon } from '../common/Icons.jsx';

export function Screen1BusinessForm({ initialData, onSubmit, isLoading }) {
  const [formData, setFormData] = useState({
    business_name_and_description: initialData?.business_name_and_description || '',
    product_or_service: initialData?.product_or_service || '',
    target_audience: initialData?.target_audience || '',
    audience_unknown: Boolean(initialData?.audience_unknown),
    business_website: initialData?.business_website || '',
  });

  const [touched, setTouched] = useState({});

  const handleTextChange = (field, value) => {
    setFormData((prev) => ({ ...prev, [field]: value }));
  };

  const handleBlur = (field) => {
    setTouched((prev) => ({ ...prev, [field]: true }));
  };

  const handleAudienceCheckbox = (checked) => {
    setFormData((prev) => ({
      ...prev,
      audience_unknown: checked,
      target_audience: checked ? '' : prev.target_audience,
    }));
    setTouched((prev) => ({ ...prev, target_audience: true }));
  };

  // Field 1 Validation: Business name & description (20-500)
  const nameDesc = formData.business_name_and_description.trim();
  const nameDescLen = nameDesc.length;
  let nameDescError = null;
  if (nameDescLen === 0) {
    nameDescError = 'Business name and description is required.';
  } else if (nameDescLen < 20) {
    nameDescError = `Please enter at least 20 characters (${nameDescLen}/20).`;
  } else if (nameDescLen > 500) {
    nameDescError = 'Please limit your description to 500 characters.';
  }

  // Field 2 Validation: Main product or service (3-300)
  const prod = formData.product_or_service.trim();
  const prodLen = prod.length;
  let prodError = null;
  if (prodLen === 0) {
    prodError = 'Main product or service line is required.';
  } else if (prodLen < 3) {
    prodError = 'Please enter at least 3 characters.';
  } else if (prodLen > 300) {
    prodError = 'Please limit this field to 300 characters.';
  }

  // Field 3 Validation: Target audience (required unless audience_unknown)
  const aud = formData.target_audience.trim();
  const audLen = aud.length;
  let audError = null;
  if (!formData.audience_unknown) {
    if (audLen === 0) {
      audError = 'Target audience is required unless you choose to infer it from your website.';
    } else if (audLen > 300) {
      audError = 'Please limit your audience to 300 characters.';
    }
  }

  // Field 4 Validation: Business website URL
  const websiteInput = formData.business_website.trim();
  const websiteNorm = normalizeUrl(websiteInput);
  let websiteError = null;
  if (!websiteInput) {
    websiteError = 'Business website URL is required.';
  } else if (!websiteNorm.valid) {
    websiteError = websiteNorm.error || 'Please enter a valid website address.';
  }

  const isValid = !nameDescError && !prodError && !audError && !websiteError;

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!isValid || isLoading) return;

    onSubmit({
      business_name_and_description: nameDesc,
      product_or_service: prod,
      target_audience: formData.audience_unknown ? '' : aud,
      audience_unknown: formData.audience_unknown,
      business_website: websiteNorm.url,
    });
  };

  return (
    <div className="single-form-layout">
      <div className="form-card-wrapper">
        <div className="form-card-header">
          <h1 className="form-card-title">Business Profile</h1>
          <p className="form-card-subtitle">
            Enter your business details below to generate an initial evidence-grounded overview.
          </p>
        </div>

        <form onSubmit={handleSubmit} noValidate autoComplete="off" className="form-stack">
            {/* Field 1: Business name and description */}
            <div className={`form-field ${touched.business_name_and_description && nameDescError ? 'has-error' : ''}`}>
              <div className="field-header">
                <label htmlFor="business_name_and_description" className="field-label">
                  Business name and description<span className="req-star" aria-hidden="true">*</span>
                </label>
                <span className="char-counter" aria-live="polite">
                  {nameDescLen}/500 {nameDescLen > 0 && nameDescLen < 20 ? '(min 20)' : ''}
                </span>
              </div>

              <textarea
                id="business_name_and_description"
                name="business_name_and_description"
                autoComplete="off"
                rows={3}
                className="input-textarea"
                placeholder="Enter your business name, what your business does, and the main value it provides to customers."
                value={formData.business_name_and_description}
                onChange={(e) => handleTextChange('business_name_and_description', e.target.value)}
                onBlur={() => handleBlur('business_name_and_description')}
                disabled={isLoading}
                maxLength={510}
                aria-invalid={Boolean(touched.business_name_and_description && nameDescError)}
              />

              {touched.business_name_and_description && nameDescError && (
                <p className="field-error-msg" role="alert">
                  <AlertCircleIcon size="sm" />
                  <span>{nameDescError}</span>
                </p>
              )}
            </div>

            {/* Field 2: Main product or service */}
            <div className={`form-field ${touched.product_or_service && prodError ? 'has-error' : ''}`}>
              <div className="field-header">
                <label htmlFor="product_or_service" className="field-label">
                  Main product or service<span className="req-star" aria-hidden="true">*</span>
                </label>
                <span className="char-counter" aria-live="polite">
                  {prodLen}/300
                </span>
              </div>

              <input
                type="text"
                id="product_or_service"
                name="product_or_service"
                autoComplete="off"
                className="input-text"
                placeholder="Describe your main product or service and what it offers to customers."
                value={formData.product_or_service}
                onChange={(e) => handleTextChange('product_or_service', e.target.value)}
                onBlur={() => handleBlur('product_or_service')}
                disabled={isLoading}
                maxLength={310}
                aria-invalid={Boolean(touched.product_or_service && prodError)}
              />

              {touched.product_or_service && prodError && (
                <p className="field-error-msg" role="alert">
                  <AlertCircleIcon size="sm" />
                  <span>{prodError}</span>
                </p>
              )}
            </div>

            {/* Field 3: Target audience */}
            <div className={`form-field ${touched.target_audience && audError ? 'has-error' : ''}`}>
              <div className="field-header">
                <label htmlFor="target_audience" className="field-label">
                  Target audience{!formData.audience_unknown && <span className="req-star" aria-hidden="true">*</span>}
                </label>
                {!formData.audience_unknown && (
                  <span className="char-counter" aria-live="polite">
                    {audLen}/300
                  </span>
                )}
              </div>

              <input
                type="text"
                id="target_audience"
                name="target_audience"
                autoComplete="off"
                className={`input-text ${formData.audience_unknown ? 'is-disabled' : ''}`}
                placeholder={
                  formData.audience_unknown
                    ? 'Target audience will be inferred from your website content'
                    : 'Describe the customers you want to reach, including their needs or interests.'
                }
                value={formData.audience_unknown ? '' : formData.target_audience}
                onChange={(e) => handleTextChange('target_audience', e.target.value)}
                onBlur={() => handleBlur('target_audience')}
                disabled={formData.audience_unknown || isLoading}
                maxLength={310}
                aria-invalid={Boolean(touched.target_audience && audError)}
              />

              {touched.target_audience && audError && (
                <p className="field-error-msg" role="alert">
                  <AlertCircleIcon size="sm" />
                  <span>{audError}</span>
                </p>
              )}

              <div className="checkbox-row">
                <label className="custom-checkbox-label">
                  <input
                    type="checkbox"
                    id="audience_unknown"
                    name="audience_unknown"
                    checked={formData.audience_unknown}
                    onChange={(e) => handleAudienceCheckbox(e.target.checked)}
                    disabled={isLoading}
                    className="native-checkbox"
                  />
                  <span className="custom-checkbox-box" />
                  <span className="checkbox-text">I am not sure yet, infer it from my website</span>
                </label>
              </div>
            </div>

            {/* Field 4: Business website */}
            <div className={`form-field ${touched.business_website && websiteError ? 'has-error' : ''}`}>
              <div className="field-header">
                <label htmlFor="business_website" className="field-label">
                  Business website<span className="req-star" aria-hidden="true">*</span>
                </label>
              </div>

              <div className="input-affix-container">
                <span className="affix-icon" aria-hidden="true">
                  <GlobeIcon size="sm" />
                </span>
                <input
                  type="text"
                  id="business_website"
                  name="business_website"
                  autoComplete="off"
                  className="input-text with-affix"
                  placeholder="Enter your business website URL."
                  value={formData.business_website}
                  onChange={(e) => handleTextChange('business_website', e.target.value)}
                  onBlur={() => handleBlur('business_website')}
                  disabled={isLoading}
                  aria-invalid={Boolean(touched.business_website && websiteError)}
                />
              </div>

              {touched.business_website && websiteError && (
                <p className="field-error-msg" role="alert">
                  <AlertCircleIcon size="sm" />
                  <span>{websiteError}</span>
                </p>
              )}
            </div>

            {/* Form Action */}
            <div className="form-submit-container">
              <button
                type="submit"
                className="btn-primary-cta"
                disabled={!isValid || isLoading}
              >
                <span>{isLoading ? 'Processing website...' : 'Continue'}</span>
                <ArrowRightIcon size="md" />
              </button>
            </div>
          </form>
        </div>
      </div>
    );
  }
