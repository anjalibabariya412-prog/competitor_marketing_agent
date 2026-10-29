import test from 'node:test';
import assert from 'node:assert/strict';
import {
  normalizeUrl,
  getDomainKey,
  isSameDomain,
  validateCompetitorList,
} from './validation.js';

test('normalizeUrl prepends https:// when missing and validates domain', () => {
  const r1 = normalizeUrl('swissbeauty.in');
  assert.equal(r1.valid, true);
  assert.equal(r1.url, 'https://swissbeauty.in/');

  const r2 = normalizeUrl('https://example.com/shop');
  assert.equal(r2.valid, true);
  assert.equal(r2.url, 'https://example.com/shop');

  const r3 = normalizeUrl('//linear.app');
  assert.equal(r3.valid, true);
  assert.equal(r3.url, 'https://linear.app/');

  const r4 = normalizeUrl('not-a-domain');
  assert.equal(r4.valid, false);

  const r5 = normalizeUrl('');
  assert.equal(r5.valid, false);
});

test('getDomainKey normalizes domains across protocols, www, ports, and casing', () => {
  assert.equal(getDomainKey('https://www.SwissBeauty.in/shop?q=1'), 'swissbeauty.in');
  assert.equal(getDomainKey('http://swissbeauty.in:8000/'), 'swissbeauty.in');
  assert.equal(getDomainKey('swissbeauty.in'), 'swissbeauty.in');
});

test('isSameDomain compares domains accurately', () => {
  assert.equal(isSameDomain('https://www.example.com', 'http://example.com/path'), true);
  assert.equal(isSameDomain('https://example.com', 'https://other.com'), false);
});

test('validateCompetitorList enforces limits, self-duplication, and duplicates', () => {
  const userWeb = 'https://mybrand.com';

  // Empty list
  const emptyRes = validateCompetitorList([], userWeb);
  assert.equal(emptyRes.isValid, false);

  // Self duplication
  const selfRes = validateCompetitorList([
    { name: 'Self', website: 'https://mybrand.com' }
  ], userWeb);
  assert.equal(selfRes.isValid, false);
  assert.match(selfRes.errors[0].website, /own business website/);

  // Duplicate competitor domain
  const dupRes = validateCompetitorList([
    { name: 'Comp A', website: 'https://competitor.com' },
    { name: 'Comp B', website: 'http://www.competitor.com/about' }
  ], userWeb);
  assert.equal(dupRes.isValid, false);
  assert.match(dupRes.errors[1].website, /Duplicate domain/);

  // Valid list
  const validRes = validateCompetitorList([
    { name: 'Comp A', website: 'https://comp-a.com' },
    { name: 'Comp B', website: 'https://comp-b.com' }
  ], userWeb);
  assert.equal(validRes.isValid, true);

  // Exceeding 5
  const tooMany = [1, 2, 3, 4, 5, 6].map(i => ({ name: `C${i}`, website: `https://c${i}.com` }));
  const maxRes = validateCompetitorList(tooMany, userWeb);
  assert.equal(maxRes.isValid, false);
  assert.match(maxRes.generalError, /Maximum 5 competitors/);
});
