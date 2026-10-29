import React from 'react';
import { ExternalLinkIcon, ShieldAlertIcon } from './Icons';

/**
 * ProvenanceBadge: renders editorial source attribution and conflict indicator.
 * @param {{
 *   provenance: { type: string, label: string, sourceUrl: string | null, hasConflict: boolean },
 *   showSourceLink?: boolean
 * }} props
 */
export function ProvenanceBadge({ provenance, showSourceLink = false }) {
  if (!provenance) return null;

  const { type, label, sourceUrl, hasConflict } = provenance;

  const hasLink = showSourceLink && type === 'from_website' && Boolean(sourceUrl);
  const hasBadge = hasConflict || hasLink || (type !== 'from_website');

  if (!hasBadge) {
    return null;
  }

  return (
    <div className="provenance-wrap">
      {hasConflict && (
        <span className="badge-flag conflict-flag" title="Information on your site conflicts with user input or other sections">
          <ShieldAlertIcon size="sm" />
          <span>Conflicting information on your site</span>
        </span>
      )}

      {hasLink ? (
        <a
          href={sourceUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="badge-provenance from-website-link"
          title={`Evidence grounded in: ${sourceUrl}`}
        >
          <span>{label}</span>
          <ExternalLinkIcon size="sm" />
        </a>
      ) : type === 'from_input' ? (
        <span className="badge-provenance from-input" title="Derived directly from your submitted business profile">
          {label}
        </span>
      ) : type === 'inference' ? (
        <span className="badge-provenance from-inference" title="Inferred by our model based on your website content and positioning">
          {label}
        </span>
      ) : null}
    </div>
  );
}

