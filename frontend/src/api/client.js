/**
 * Central API Client for Competitor Marketing Agent.
 * All outbound requests route through here with typed signatures and clean error handling.
 */

import { findCompetitorsMock, submitCompetitorsForAnalysisMock } from './mocks.js';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';

/**
 * Calls POST /api/v1/business-overview
 * Crawls business website up to 8 pages and generates structured overview.
 * 
 * @param {import('./types.js').BusinessFormPayload} payload
 * @param {AbortSignal} [signal]
 * @returns {Promise<import('./types.js').BusinessOverviewResponse>}
 */
export async function fetchBusinessOverview(payload, signal) {
  const requestBody = {
    business_name_and_description: payload.business_name_and_description.trim(),
    product_or_service: payload.product_or_service.trim(),
    target_audience: payload.audience_unknown ? null : (payload.target_audience?.trim() || null),
    audience_unknown: Boolean(payload.audience_unknown),
    business_website: payload.business_website.trim(),
  };

  let response;
  try {
    response = await fetch(`${API_BASE_URL}/api/v1/business-overview`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
      body: JSON.stringify(requestBody),
      signal,
    });
  } catch (err) {
    if (err.name === 'AbortError') {
      throw new Error('Analysis was cancelled.');
    }
    throw new Error('Unable to reach the analysis server. Please ensure the backend is running.');
  }

  if (!response.ok) {
    let message = 'We could not generate the overview. Please try again.';
    try {
      const errData = await response.json();
      if (errData?.detail) {
        if (Array.isArray(errData.detail)) {
          // Format validation errors cleanly
          message = errData.detail.map((d) => d.msg || 'Invalid field').join('. ');
        } else if (typeof errData.detail === 'string') {
          message = errData.detail;
        }
      }
    } catch {
      // Fallback message
    }
    throw new Error(message);
  }

  const data = await response.json();
  return data;
}

/**
 * Discovers candidate competitors autonomously.
 * Uses mock service until real endpoint is available.
 * TODO: Replace findCompetitorsMock with real backend endpoint POST /api/v1/competitors/discover
 */
export async function discoverCompetitors(params, signal) {
  return findCompetitorsMock(params, signal);
}

/**
 * Submits the confirmed competitor list for comparative report synthesis.
 * Uses mock service until real endpoint is available.
 * TODO: Replace submitCompetitorsForAnalysisMock with real backend endpoint POST /api/v1/competitor-analysis
 */
export async function analyzeCompetitors(params, signal) {
  return submitCompetitorsForAnalysisMock(params, signal);
}

