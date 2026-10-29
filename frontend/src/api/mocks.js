/**
 * Mock services for endpoints that are not yet built in the backend.
 * Each mock is isolated behind a clean interface with explicit TODO comments.
 */

/**
 * Mock 1: Discover competitor candidates automatically.
 * TODO: Replace with real backend endpoint when available:
 *   POST /api/v1/competitors/discover
 *   Request: { business_website: string, product_or_service: string, business_description?: string }
 *   Response: { candidates: Array<{ id: string, name: string, website: string, reason: string }> }
 *
 * @param {Object} params
 * @param {string} params.businessWebsite
 * @param {string} [params.productOrService]
 * @param {string} [params.businessName]
 * @param {AbortSignal} [signal]
 * @returns {Promise<Array<{ id: string, name: string, website: string, reason: string }>>}
 */
export async function findCompetitorsMock({ businessWebsite, productOrService, businessName: _businessName }, signal) {
  // Simulate network latency (1.2s)
  await new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, 1200);
    if (signal) {
      signal.addEventListener('abort', () => {
        clearTimeout(timer);
        reject(new Error('Discovery cancelled.'));
      });
    }
  });

  const domain = (businessWebsite || '').toLowerCase();

  // Intelligent context-aware mock candidates
  if (domain.includes('swiss') || domain.includes('beauty') || domain.includes('cosmetics')) {
    return [
      {
        id: 'comp_1',
        name: 'Sugar Cosmetics',
        website: 'https://sugarcosmetics.com',
        reason: 'Direct Indian market alternative with prominent color cosmetics and budget-friendly lip and eye products.',
      },
      {
        id: 'comp_2',
        name: 'Maybelline New York (India)',
        website: 'https://maybelline.co.in',
        reason: 'Category benchmark for everyday foundations, concealers, and long-wear mascaras at retail drugstores.',
      },
      {
        id: 'comp_3',
        name: 'Insight Cosmetics',
        website: 'https://insightcosmetics.in',
        reason: 'Ultra-budget beauty competitor with high volume sales in concealers and everyday makeup essentials.',
      },
    ];
  }

  if (domain.includes('linear') || domain.includes('saas') || domain.includes('software')) {
    return [
      {
        id: 'comp_1',
        name: 'Jira Software',
        website: 'https://atlassian.com/software/jira',
        reason: 'Enterprise standard for software issue tracking, agile boards, and project management workflows.',
      },
      {
        id: 'comp_2',
        name: 'Height',
        website: 'https://height.app',
        reason: 'Autonomous product development tool competing on keyboard shortcuts and real-time collaboration.',
      },
      {
        id: 'comp_3',
        name: 'Shortcut',
        website: 'https://shortcut.com',
        reason: 'Developer-centric issue tracker and sprint planner built specifically for modern product teams.',
      },
    ];
  }

  // General default competitor candidates
  return [
    {
      id: 'comp_1',
      name: 'Market Leader Alpha',
      website: 'https://example-competitor-alpha.com',
      reason: `Direct alternative targeting similar customer segments for ${productOrService || 'your offerings'}.`,
    },
    {
      id: 'comp_2',
      name: 'Vanguard Services',
      website: 'https://example-competitor-beta.com',
      reason: 'Competes on premium service delivery and fast customer turnaround.',
    },
    {
      id: 'comp_3',
      name: 'Eco Choice Co',
      website: 'https://example-competitor-gamma.com',
      reason: 'Alternative provider offering bundled pricing and digital-first customer onboarding.',
    },
  ];
}

/**
 * Mock 2: Submit competitors for in-depth competitor analysis report generation.
 * TODO: Replace with real backend endpoint when available:
 *   POST /api/v1/competitor-analysis
 *   Request: {
 *     business_website: string,
 *     business_overview: Object,
 *     competitors: Array<{ name: string, website: string, social_link?: string }>
 *   }
 *   Response: { job_id: string, status: string, report?: Object }
 *
 * @param {Object} params
 * @param {string} params.businessWebsite
 * @param {Array<{ name: string, website: string, social_link?: string }>} params.competitors
 * @param {Object} [params.businessOverview]
 * @param {AbortSignal} [signal]
 * @returns {Promise<{ status: string, reportData: Object }>}
 */
export async function submitCompetitorsForAnalysisMock({ businessWebsite, competitors, businessOverview }, signal) {
  // Simulate progressive generation delay (2.5s)
  await new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, 2500);
    if (signal) {
      signal.addEventListener('abort', () => {
        clearTimeout(timer);
        reject(new Error('Analysis cancelled.'));
      });
    }
  });

  return {
    status: 'success',
    reportData: {
      generated_at: new Date().toISOString(),
      business_name: businessOverview?.business_name || 'Your Business',
      website_url: businessWebsite,
      competitors: competitors.map((c) => ({
        name: c.name,
        website: c.website,
        social_link: c.social_link || null,
      })),
    },
  };
}
