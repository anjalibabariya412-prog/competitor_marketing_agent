/**
 * Central API Client for Competitor Marketing Agent.
 * All outbound requests route through here with typed signatures and clean error handling.
 */

const API_BASE_URL = import.meta.env?.VITE_API_BASE_URL || 'http://127.0.0.1:8000';

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
 * Calls POST /api/v1/business-overview/stream
 * Streams real-time pipeline stages via SSE and delivers final BusinessOverviewResponse on 'done'.
 *
 * @param {import('./types.js').BusinessFormPayload} payload
 * @param {(event: { stage: string, meta?: any, payload?: any, message?: string }) => void} onEvent
 * @param {AbortSignal} [signal]
 * @returns {Promise<import('./types.js').BusinessOverviewResponse>}
 */
export async function fetchBusinessOverviewStream(payload, onEvent, signal) {
  const requestBody = {
    business_name_and_description: payload.business_name_and_description.trim(),
    product_or_service: payload.product_or_service.trim(),
    target_audience: payload.audience_unknown ? null : (payload.target_audience?.trim() || null),
    audience_unknown: Boolean(payload.audience_unknown),
    business_website: payload.business_website.trim(),
  };

  let response;
  try {
    response = await fetch(`${API_BASE_URL}/api/v1/business-overview/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
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
          message = errData.detail.map((d) => d.msg || 'Invalid field').join('. ');
        } else if (typeof errData.detail === 'string') {
          message = errData.detail;
        }
      }
    } catch {
      // Fallback
    }
    throw new Error(message);
  }

  if (!response.body || typeof response.body.getReader !== 'function') {
    throw new Error('ReadableStream not supported by browser environment.');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let finalPayload = null;

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith(':')) continue;

        if (trimmed.startsWith('data: ')) {
          const jsonStr = trimmed.slice(6).trim();
          let event;
          try {
            event = JSON.parse(jsonStr);
          } catch {
            continue;
          }

          if (onEvent && typeof onEvent === 'function') {
            onEvent(event);
          }

          if (event.stage === 'done') {
            finalPayload = event.payload || event.data;
          } else if (event.stage === 'error' || event.stage === 'scrape_failed') {
            throw new Error(event.message || 'Analysis failed. Please check the website and try again.');
          }
        }
      }
    }

    if (buffer.trim().startsWith('data: ')) {
      const jsonStr = buffer.trim().slice(6).trim();
      try {
        const event = JSON.parse(jsonStr);
        if (onEvent && typeof onEvent === 'function') {
          onEvent(event);
        }
        if (event.stage === 'done') {
          finalPayload = event.payload || event.data;
        } else if (event.stage === 'error' || event.stage === 'scrape_failed') {
          throw new Error(event.message || 'Analysis failed. Please check the website and try again.');
        }
      } catch (err) {
        if (err.message && !err.message.includes('JSON')) throw err;
      }
    }
  } finally {
    reader.releaseLock();
  }

  if (!finalPayload) {
    throw new Error('Stream ended without delivering business overview data.');
  }

  return finalPayload;
}


