import test from 'node:test';
import assert from 'node:assert/strict';
import { deriveProvenance, adaptBusinessOverview } from './overviewAdapter.js';

test('deriveProvenance derives website, input, and inference with conflict checks', () => {
  // Website provenance
  const p1 = deriveProvenance({ is_explicit: true, source_url: 'https://example.com/about' });
  assert.equal(p1.type, 'from_website');
  assert.equal(p1.label, 'From your website');
  assert.equal(p1.sourceUrl, 'https://example.com/about');
  assert.equal(p1.hasConflict, false);

  // Input provenance
  const p2 = deriveProvenance({ is_explicit: true });
  assert.equal(p2.type, 'from_input');
  assert.equal(p2.label, 'From your input');
  assert.equal(p2.sourceUrl, null);

  // Inference provenance
  const p3 = deriveProvenance({ is_inferred: true, source_url: 'https://example.com/pricing' });
  assert.equal(p3.type, 'inference');
  assert.equal(p3.label, 'Our inference');

  // Conflict flag
  const p4 = deriveProvenance({ has_conflict: true, source_url: 'https://example.com' });
  assert.equal(p4.hasConflict, true);
});

test('adaptBusinessOverview adapts response with fallback handling', () => {
  const raw = {
    status: 'success',
    website_url: 'https://swissbeauty.in',
    execution_time_seconds: 14.2,
    business_overview: {
      business_name: 'Swiss Beauty',
      business_description: 'Color cosmetics brand.',
      sources_used: ['https://swissbeauty.in', 'https://swissbeauty.in/products'],
      products_and_services: [
        {
          name: 'Concealer',
          description: 'Liquid concealer',
          features: ['Waterproof'],
          benefits: ['Full coverage'],
          source_url: 'https://swissbeauty.in/products',
        }
      ],
      target_audience: {
        description: 'Young makeup enthusiasts',
        is_explicit: false,
      },
      positioning_and_value_propositions: [
        { statement: 'Affordable luxury', is_explicit: true, source_url: 'https://swissbeauty.in' }
      ],
      information_gaps: [
        { category: 'Pricing', observation: 'Wholesale tiers not published.' }
      ],
      key_findings: [
        { statement: 'Free shipping on orders over 499', is_inferred: false, has_conflict: false, source_url: 'https://swissbeauty.in' }
      ]
    }
  };

  const adapted = adaptBusinessOverview(raw);
  assert.equal(adapted.businessName, 'Swiss Beauty');
  assert.equal(adapted.pageCount, 2);
  assert.equal(adapted.products.length, 1);
  assert.equal(adapted.products[0].provenance.type, 'from_website');
  assert.equal(adapted.audience.provenance.type, 'inference');
  assert.equal(adapted.informationGaps.length, 1);
});

