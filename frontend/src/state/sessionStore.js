/**
 * Session State Store for Competitor Marketing Agent.
 * Manages workflow state, theme, and downstream competitor analysis in sessionStorage.
 * Form field inputs and generated business overview are never persisted across browser reloads or page refreshes.
 */

const STORAGE_KEY = 'cma_session_v2';

export const EMPTY_FORM_DATA = {
  business_name_and_description: '',
  product_or_service: '',
  target_audience: '',
  audience_unknown: false,
  business_website: '',
};

export const INITIAL_STATE = {
  currentStep: 1, // 1: Business Information, 2: Business Overview, 3: Competitor Analysis
  formData: { ...EMPTY_FORM_DATA },
  overviewResponse: null,
  competitorMode: 'manual', // 'manual' | 'discover'
  competitors: [
    { id: 'comp_1', name: '', website: '' }
  ],
  discoveredCandidates: [],
  theme: 'light',
};

/**
 * Loads session state from sessionStorage with fallback to initial state.
 * Form input fields and generated overview are strictly reset on every fresh load / browser refresh,
 * ensuring the initial form (Step 1) is always displayed.
 * @returns {typeof INITIAL_STATE}
 */
export function loadSessionState() {
  try {
    const raw = typeof sessionStorage !== 'undefined' ? sessionStorage.getItem(STORAGE_KEY) : null;
    if (!raw) return { ...INITIAL_STATE, formData: { ...EMPTY_FORM_DATA } };
    const parsed = JSON.parse(raw);
    return {
      ...INITIAL_STATE,
      ...parsed,
      // Always reset to initial business form on browser refresh / page reload
      currentStep: 1,
      overviewResponse: null,
      formData: { ...EMPTY_FORM_DATA },
      competitors: Array.isArray(parsed.competitors) && parsed.competitors.length > 0
        ? parsed.competitors
        : INITIAL_STATE.competitors,
    };
  } catch {
    return { ...INITIAL_STATE, formData: { ...EMPTY_FORM_DATA } };
  }
}

/**
 * Persists session state to sessionStorage, omitting form inputs and overview state
 * so browser refresh always returns to the initial form.
 * @param {Object} state 
 */
export function saveSessionState(state) {
  try {
    if (typeof sessionStorage === 'undefined') return;
    const {
      formData: _ignoredForm,
      overviewResponse: _ignoredOverview,
      currentStep: _ignoredStep,
      ...persistedState
    } = state;
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
      ...persistedState,
      currentStep: 1,
      overviewResponse: null,
      formData: { ...EMPTY_FORM_DATA },
    }));
  } catch (err) {
    console.warn('Unable to persist session state:', err);
  }
}

/**
 * Clears session state from sessionStorage and returns initial state.
 */
export function clearSessionState() {
  try {
    if (typeof sessionStorage !== 'undefined') {
      sessionStorage.removeItem(STORAGE_KEY);
    }
  } catch {}
  return { ...INITIAL_STATE, formData: { ...EMPTY_FORM_DATA } };
}
