import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fetchBusinessOverviewStream } from './client.js';

function createMockStream(chunks) {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

test('fetchBusinessOverviewStream processes SSE stages and delivers final payload', async () => {
  const originalFetch = global.fetch;

  const events = [];
  const mockChunks = [
    ': keep-alive\n\n',
    'data: {"stage": "visiting_website", "meta": {"website_url": "https://example.com"}}\n\n',
    'data: {"stage": "reading_pages", "meta": {"page_count": 5}}\n\n',
    'data: {"stage": "writing_overview", "meta": {}}\n\n',
    'data: {"stage": "done", "payload": {"business_name": "Test Co", "what_you_offer": []}}\n\n',
  ];

  global.fetch = async () => ({
    ok: true,
    body: createMockStream(mockChunks),
  });

  try {
    const payload = {
      business_name_and_description: 'Test Co',
      product_or_service: 'SaaS',
      business_website: 'https://example.com',
      audience_unknown: true,
    };

    const result = await fetchBusinessOverviewStream(payload, (evt) => {
      events.push(evt);
    });

    assert.equal(result.business_name, 'Test Co');
    assert.equal(events.length, 4);
    assert.equal(events[0].stage, 'visiting_website');
    assert.equal(events[1].stage, 'reading_pages');
    assert.equal(events[2].stage, 'writing_overview');
    assert.equal(events[3].stage, 'done');
  } finally {
    global.fetch = originalFetch;
  }
});

test('fetchBusinessOverviewStream throws on scrape_failed event', async () => {
  const originalFetch = global.fetch;

  const mockChunks = [
    'data: {"stage": "visiting_website", "meta": {}}\n\n',
    'data: {"stage": "scrape_failed", "message": "Failed to scrape homepage."}\n\n',
  ];

  global.fetch = async () => ({
    ok: true,
    body: createMockStream(mockChunks),
  });

  try {
    const payload = {
      business_name_and_description: 'Test Co',
      product_or_service: 'SaaS',
      business_website: 'https://invalid-site.xyz',
      audience_unknown: true,
    };

    await assert.rejects(
      async () => {
        await fetchBusinessOverviewStream(payload, () => {});
      },
      {
        message: 'Failed to scrape homepage.',
      }
    );
  } finally {
    global.fetch = originalFetch;
  }
});

