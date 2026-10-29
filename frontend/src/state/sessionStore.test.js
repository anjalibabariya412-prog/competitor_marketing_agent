import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { 
  loadSessionState, 
  saveSessionState, 
  clearSessionState, 
  EMPTY_FORM_DATA 
} from './sessionStore.js';

// Setup mock sessionStorage in Node test environment
const mockStorage = new Map();
globalThis.sessionStorage = {
  getItem: (key) => mockStorage.get(key) || null,
  setItem: (key, val) => mockStorage.set(key, String(val)),
  removeItem: (key) => mockStorage.delete(key),
  clear: () => mockStorage.clear(),
};

beforeEach(() => {
  mockStorage.clear();
});

test('loadSessionState returns completely empty form fields on fresh load', () => {
  const session = loadSessionState();
  assert.deepEqual(session.formData, EMPTY_FORM_DATA);
  assert.equal(session.formData.business_name_and_description, '');
  assert.equal(session.formData.product_or_service, '');
  assert.equal(session.formData.target_audience, '');
  assert.equal(session.formData.audience_unknown, false);
  assert.equal(session.formData.business_website, '');
});

test('loadSessionState clears form fields even if sessionStorage has prior form entries', () => {
  // Simulate previous session data in sessionStorage
  mockStorage.set('cma_session_v2', JSON.stringify({
    currentStep: 1,
    formData: {
      business_name_and_description: 'Previous company description that was entered',
      product_or_service: 'Previous product line',
      target_audience: 'Previous target audience',
      audience_unknown: true,
      business_website: 'https://example.com',
    },
    theme: 'dark',
  }));

  // Browser refresh triggers loadSessionState()
  const refreshedSession = loadSessionState();

  // Form fields must be strictly reset and empty
  assert.deepEqual(refreshedSession.formData, EMPTY_FORM_DATA);
  assert.equal(refreshedSession.formData.business_name_and_description, '');
  assert.equal(refreshedSession.formData.product_or_service, '');
  assert.equal(refreshedSession.formData.target_audience, '');
  assert.equal(refreshedSession.formData.audience_unknown, false);
  assert.equal(refreshedSession.formData.business_website, '');
  
  // Non-form settings like theme are preserved
  assert.equal(refreshedSession.theme, 'dark');
});

test('saveSessionState omits form data from sessionStorage', () => {
  const activeSession = {
    currentStep: 1,
    formData: {
      business_name_and_description: 'Live active form typing',
      product_or_service: 'Live product',
      target_audience: 'Live audience',
      audience_unknown: false,
      business_website: 'https://mysite.com',
    },
    theme: 'light',
  };

  saveSessionState(activeSession);

  const storedRaw = mockStorage.get('cma_session_v2');
  assert.ok(storedRaw);
  const storedParsed = JSON.parse(storedRaw);

  // Stored formData must be empty
  assert.deepEqual(storedParsed.formData, EMPTY_FORM_DATA);
});

test('clearSessionState clears storage and returns empty form data', () => {
  mockStorage.set('cma_session_v2', '{"dummy": true}');
  const state = clearSessionState();
  assert.equal(mockStorage.size, 0);
  assert.deepEqual(state.formData, EMPTY_FORM_DATA);
});

