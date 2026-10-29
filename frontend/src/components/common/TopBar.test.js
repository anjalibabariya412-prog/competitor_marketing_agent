import { test } from 'node:test';
import assert from 'node:assert/strict';
import { WIZARD_STEPS } from '../../constants/steps.js';

test('WIZARD_STEPS defines exactly 3 steps for the workflow', () => {
  assert.equal(WIZARD_STEPS.length, 3);
  
  assert.deepEqual(WIZARD_STEPS[0], { id: 1, label: 'Business Information' });
  assert.deepEqual(WIZARD_STEPS[1], { id: 2, label: 'Business Overview' });
  assert.deepEqual(WIZARD_STEPS[2], { id: 3, label: 'Competitor Analysis' });
});

test('WIZARD_STEPS does not contain outdated 6-step wizard labels', () => {
  const labels = WIZARD_STEPS.map((s) => s.label);
  assert.ok(!labels.includes('Positioning & Offers'));
  assert.ok(!labels.includes('Marketing & Channels'));
  assert.ok(!labels.includes('Final Report'));
});
