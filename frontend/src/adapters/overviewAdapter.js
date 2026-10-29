/**
 * Adapter module isolating backend Business Overview response to view models.
 * Gracefully adapts schema variations, missing fields, and future changes.
 */

/**
 * Derives provenance classification for an item or claim.
 * @param {Object} item 
 * @param {boolean} [item.is_explicit]
 * @param {boolean} [item.is_inferred]
 * @param {string|null} [item.source_url]
 * @param {boolean} [item.has_conflict]
 * @returns {{ type: 'from_website' | 'from_input' | 'inference', label: string, sourceUrl: string | null, hasConflict: boolean }}
 */
export function deriveProvenance(item = {}) {
  const sourceUrl = item.source_url || (Array.isArray(item.source_urls) && item.source_urls[0]) || null;
  const isExplicit = item.is_explicit !== false;
  const isInferred = Boolean(item.is_inferred) || !isExplicit;
  const hasConflict = Boolean(item.has_conflict);

  if (isInferred) {
    return {
      type: 'inference',
      label: 'Our inference',
      sourceUrl,
      hasConflict,
    };
  }

  if (sourceUrl) {
    return {
      type: 'from_website',
      label: 'From your website',
      sourceUrl,
      hasConflict,
    };
  }

  return {
    type: 'from_input',
    label: 'From your input',
    sourceUrl: null,
    hasConflict,
  };
}

/**
 * Normalizes and adapts the raw business overview response for the Overview screen.
 * @param {import('../api/types.js').BusinessOverviewResponse} rawResponse 
 * @returns {Object} Clean ViewModel
 */
export function adaptBusinessOverview(rawResponse) {
  if (!rawResponse || typeof rawResponse !== 'object') {
    return null;
  }

  const overview = rawResponse.business_overview || {};
  const websiteUrl = rawResponse.website_url || overview.website_url || '';
  const businessName = overview.business_name || 'Your Business';
  const businessDescription = overview.business_description || 'No business description provided.';

  // Sources and page count
  const sourcesUsed = Array.isArray(overview.sources_used) ? overview.sources_used.filter(Boolean) : [];
  const pageCount = sourcesUsed.length || (rawResponse.total_pages_analyzed || 0);

  // 1. What you offer (Product cards)
  let rawOfferings = overview.products_and_services;
  if (!Array.isArray(rawOfferings) && rawOfferings?.featured_items) {
    rawOfferings = rawOfferings.featured_items;
  } else if (!Array.isArray(rawOfferings)) {
    rawOfferings = [];
  }

  const products = rawOfferings.map((p, idx) => {
    if (typeof p === 'string') {
      return {
        id: `prod_${idx}`,
        name: p,
        description: null,
        features: [],
        benefits: [],
        pricing: null,
        variants: [],
        provenance: deriveProvenance({}),
      };
    }

    return {
      id: `prod_${idx}`,
      name: p.name || `Offering ${idx + 1}`,
      description: p.description || null,
      features: Array.isArray(p.features) ? p.features : [],
      benefits: Array.isArray(p.benefits) ? p.benefits : [],
      pricing: p.pricing || null,
      variants: Array.isArray(p.variants) ? p.variants : [],
      provenance: deriveProvenance(p),
    };
  });

  // 2. Who you serve (Target audience)
  const rawAudience = overview.target_audience;
  let audience = {
    description: 'Your audience is inferred from your website offerings and tone.',
    userStated: null,
    provenance: deriveProvenance({}),
    hasConflict: false,
  };

  if (typeof rawAudience === 'string') {
    audience.description = rawAudience;
    audience.provenance = deriveProvenance({});
  } else if (rawAudience && typeof rawAudience === 'object') {
    audience = {
      description: rawAudience.description || rawAudience.website_inferred || 'Audience inferred from your website.',
      userStated: rawAudience.user_stated_audience || rawAudience.user_stated || null,
      provenance: deriveProvenance(rawAudience),
      hasConflict: Boolean(rawAudience.has_conflict),
      demographics: Array.isArray(rawAudience.core_demographics) ? rawAudience.core_demographics : [],
      needs: Array.isArray(rawAudience.key_needs) ? rawAudience.key_needs : [],
    };
  }

  // 3. How you position yourself (Value propositions)
  const rawProps = Array.isArray(overview.positioning_and_value_propositions)
    ? overview.positioning_and_value_propositions
    : [];

  const positioning = rawProps.map((vp, idx) => {
    if (typeof vp === 'string') {
      return {
        id: `vp_${idx}`,
        statement: vp,
        provenance: deriveProvenance({ is_explicit: true }),
      };
    }
    return {
      id: `vp_${idx}`,
      statement: vp.statement || '',
      provenance: deriveProvenance(vp),
    };
  }).filter((item) => Boolean(item.statement));

  // 4. Key findings
  const rawFindings = Array.isArray(overview.key_findings) ? overview.key_findings : [];
  const findings = rawFindings.map((f, idx) => {
    if (typeof f === 'string') {
      return {
        id: `find_${idx}`,
        statement: f,
        type: 'general',
        provenance: deriveProvenance({}),
      };
    }
    return {
      id: `find_${idx}`,
      statement: f.statement || '',
      type: f.finding_type || 'insight',
      provenance: deriveProvenance(f),
    };
  }).filter((f) => Boolean(f.statement));

  // 5. What we couldn't find (Information gaps - gentle notice)
  const rawGaps = Array.isArray(overview.information_gaps) ? overview.information_gaps : [];
  const informationGaps = rawGaps.map((g, idx) => {
    if (typeof g === 'string') {
      return {
        id: `gap_${idx}`,
        category: 'Information gap',
        observation: g,
      };
    }
    return {
      id: `gap_${idx}`,
      category: g.category || 'Information gap',
      observation: g.observation || '',
    };
  }).filter((g) => Boolean(g.observation));

  return {
    businessName,
    websiteUrl,
    businessDescription,
    pageCount,
    executionTimeSeconds: rawResponse.execution_time_seconds ?? null,
    products,
    audience,
    positioning,
    findings,
    informationGaps,
    sourcesUsed,
  };
}

