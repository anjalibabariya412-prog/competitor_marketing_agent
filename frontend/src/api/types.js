/**
 * API Type Definitions (JSDoc)
 * Reflects the backend Pydantic contract and handles schema evolution gracefully.
 */

/**
 * @typedef {Object} BusinessFormPayload
 * @property {string} business_name_and_description - Required (20-500 chars)
 * @property {string} product_or_service - Required (3-300 chars)
 * @property {string|null} target_audience - Optional if audience_unknown is true
 * @property {boolean} audience_unknown - Whether user is unsure about target audience
 * @property {string} business_website - Valid HTTP/HTTPS website URL
 */

/**
 * @typedef {Object} RawProductServiceItem
 * @property {string} name
 * @property {string} [description]
 * @property {string[]} [features]
 * @property {string[]} [benefits]
 * @property {string|null} [source_url]
 * @property {string[]} [source_urls]
 * @property {Object} [pricing]
 * @property {string[]} [variants]
 */

/**
 * @typedef {Object} RawTargetAudience
 * @property {string} [description]
 * @property {boolean} [is_explicit]
 * @property {string|null} [source_url]
 * @property {string|null} [user_stated_audience]
 * @property {boolean} [has_conflict]
 */

/**
 * @typedef {Object} RawValueProposition
 * @property {string} statement
 * @property {boolean} [is_explicit]
 * @property {string|null} [source_url]
 */

/**
 * @typedef {Object} RawInformationGap
 * @property {string} [category]
 * @property {string} observation
 */

/**
 * @typedef {Object} RawKeyFinding
 * @property {string} statement
 * @property {string} [finding_type]
 * @property {string|null} [source_url]
 * @property {boolean} [is_inferred]
 * @property {boolean} [has_conflict]
 */

/**
 * @typedef {Object} RawBusinessOverview
 * @property {string} [business_name]
 * @property {string} website_url
 * @property {string} business_description
 * @property {RawProductServiceItem[]} [products_and_services]
 * @property {RawTargetAudience} [target_audience]
 * @property {RawValueProposition[]} [positioning_and_value_propositions]
 * @property {string[]} [conflicts]
 * @property {RawInformationGap[]} [information_gaps]
 * @property {RawKeyFinding[]} [key_findings]
 * @property {string[]} [sources_used]
 */

/**
 * @typedef {Object} BusinessOverviewResponse
 * @property {string} status
 * @property {RawBusinessOverview} business_overview
 * @property {string} website_url
 * @property {number} [execution_time_seconds]
 */

/**
 * @typedef {Object} CompetitorCandidate
 * @property {string} id
 * @property {string} name
 * @property {string} website
 * @property {string} reason
 * @property {string|null} [social_link]
 */

/**
 * @typedef {Object} CompetitorAnalysisReport
 * @property {string} status
 * @property {string} business_name
 * @property {string[]} competitors
 * @property {Object} sections
 */
export {};

