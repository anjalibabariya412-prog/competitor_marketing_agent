/**
 * Pure validation and URL normalization helpers for Competitor Marketing Agent.
 */

/**
 * Normalizes a website URL:
 * - Prepends https:// if protocol is missing
 * - Trims whitespace
 * - Validates host and protocol
 * @param {string} input 
 * @returns {{ valid: boolean, url: string, error?: string }}
 */
export function normalizeUrl(input) {
  if (!input || typeof input !== 'string') {
    return { valid: false, url: '', error: 'URL is required.' };
  }

  let clean = input.trim();
  if (!clean) {
    return { valid: false, url: '', error: 'URL cannot be empty.' };
  }

  if (clean.startsWith('//')) {
    clean = `https:${clean}`;
  } else if (!clean.includes('://')) {
    clean = `https://${clean}`;
  }

  try {
    const parsed = new URL(clean);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return { valid: false, url: '', error: 'URL must use http or https.' };
    }

    const hostname = parsed.hostname.toLowerCase();
    if (!hostname || !hostname.includes('.') || hostname.endsWith('.')) {
      return { valid: false, url: '', error: 'Please enter a valid domain (e.g. example.com).' };
    }

    if (hostname === 'localhost' || hostname.endsWith('.localhost')) {
      return { valid: false, url: '', error: 'Localhost is not supported.' };
    }

    // Normalized canonical URL string
    return { valid: true, url: parsed.origin + parsed.pathname + parsed.search };
  } catch {
    return { valid: false, url: '', error: 'Please enter a valid website address.' };
  }
}

/**
 * Extracts a normalized domain key for comparison:
 * Strips scheme, leading 'www.', ports, paths, and converts to lowercase.
 * Example: "https://www.Example.com/path/" -> "example.com"
 * @param {string} input 
 * @returns {string}
 */
export function getDomainKey(input) {
  if (!input || typeof input !== 'string') return '';
  const { url, valid } = normalizeUrl(input);
  if (!valid) {
    let raw = input.trim().toLowerCase().replace(/^https?:\/\//, '').replace(/^\/\//, '');
    raw = raw.split('/')[0].split('?')[0].split(':')[0];
    if (raw.startsWith('www.')) raw = raw.slice(4);
    return raw;
  }
  try {
    const parsed = new URL(url);
    let host = parsed.hostname.toLowerCase();
    if (host.startsWith('www.')) host = host.slice(4);
    return host;
  } catch {
    return '';
  }
}

/**
 * Checks whether two URLs belong to the same root domain.
 * @param {string} urlA 
 * @param {string} urlB 
 * @returns {boolean}
 */
export function isSameDomain(urlA, urlB) {
  const domainA = getDomainKey(urlA);
  const domainB = getDomainKey(urlB);
  if (!domainA || !domainB) return false;
  return domainA === domainB;
}

/**
 * Validates competitor list constraints:
 * - Min 1 competitor
 * - Max 5 competitors
 * - Names and websites required
 * - Cannot duplicate user's own business domain
 * - Cannot duplicate another competitor's domain
 * @param {Array<{ name: string, website: string }>} competitors 
 * @param {string} userWebsite 
 * @returns {{ isValid: boolean, errors: Record<number, { name?: string, website?: string }>, generalError?: string }}
 */
export function validateCompetitorList(competitors, userWebsite = '') {
  const errors = {};
  let hasErrors = false;

  if (!Array.isArray(competitors) || competitors.length === 0) {
    return { isValid: false, errors: {}, generalError: 'Please add at least one competitor.' };
  }

  if (competitors.length > 5) {
    return { isValid: false, errors: {}, generalError: 'Maximum 5 competitors allowed.' };
  }

  const userDomain = getDomainKey(userWebsite);
  const seenDomains = new Map(); // domainKey -> index

  competitors.forEach((comp, idx) => {
    const rowErrors = {};

    const name = comp.name?.trim() || '';
    if (!name) {
      rowErrors.name = 'Competitor name is required.';
    } else if (name.length < 2) {
      rowErrors.name = 'Competitor name is too short.';
    }

    const website = comp.website?.trim() || '';
    if (!website) {
      rowErrors.website = 'Competitor website is required.';
    } else {
      const norm = normalizeUrl(website);
      if (!norm.valid) {
        rowErrors.website = norm.error || 'Invalid website address.';
      } else {
        const domain = getDomainKey(norm.url);
        if (userDomain && domain === userDomain) {
          rowErrors.website = 'This is your own business website. Please enter a competitor.';
        } else if (seenDomains.has(domain)) {
          rowErrors.website = `Duplicate domain with competitor #${seenDomains.get(domain) + 1}.`;
        } else {
          seenDomains.set(domain, idx);
        }
      }
    }

    if (Object.keys(rowErrors).length > 0) {
      errors[idx] = rowErrors;
      hasErrors = true;
    }
  });

  return {
    isValid: !hasErrors,
    errors,
    generalError: hasErrors ? 'Please fix the errors in your competitor list.' : undefined,
  };
}
