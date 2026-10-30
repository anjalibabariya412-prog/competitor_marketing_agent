import React, { useState, useEffect, useRef } from 'react';
import { 
  loadSessionState, 
  saveSessionState 
} from './state/sessionStore.js';
import { TopBar } from './components/common/TopBar.jsx';
import { Screen1BusinessForm } from './components/screens/Screen1BusinessForm.jsx';
import { Screen1Loading } from './components/screens/Screen1Loading.jsx';
import { Screen2Overview } from './components/screens/Screen2Overview.jsx';
import { Screen3Competitors } from './components/screens/Screen3Competitors.jsx';
import { Screen4PositioningOffers } from './components/screens/Screen4PositioningOffers.jsx';
import { Screen5MarketingChannels } from './components/screens/Screen5MarketingChannels.jsx';
import { Screen6FinalReport } from './components/screens/Screen6FinalReport.jsx';
import { fetchBusinessOverview, analyzeCompetitors } from './api/client.js';
import './App.css';

export default function App() {
  const [session, setSession] = useState(() => loadSessionState());
  const [isLoading, setIsLoading] = useState(false);
  const [apiError, setApiError] = useState(null);

  const abortRef = useRef(null);

  // Sync session state to sessionStorage
  useEffect(() => {
    saveSessionState(session);
  }, [session]);

  const handleStepClick = (targetStep) => {
    if (targetStep < session.currentStep) {
      setSession((prev) => ({ ...prev, currentStep: targetStep }));
      setApiError(null);
    }
  };

  // Step 1 Submission: Call POST /api/v1/business-overview
  const handleBusinessFormSubmit = async (formData) => {
    setSession((prev) => ({ ...prev, formData }));
    setApiError(null);
    setIsLoading(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetchBusinessOverview(formData, controller.signal);
      setSession((prev) => ({
        ...prev,
        overviewResponse: response,
        currentStep: 2, // Advance to Step 2: Business Overview
      }));
    } catch (err) {
      if (err.message !== 'Analysis was cancelled.') {
        setApiError(err.message || 'Unable to generate business overview. Please try again.');
      }
    } finally {
      setIsLoading(false);
      abortRef.current = null;
    }
  };

  const handleCancelLoading = () => {
    if (abortRef.current) {
      abortRef.current.abort();
    }
    setIsLoading(false);
    setApiError(null);
  };

  // Navigation handlers
  const handleBackToForm = () => {
    setSession((prev) => ({ ...prev, currentStep: 1 }));
    setApiError(null);
  };

  const handleContinueToCompetitors = () => {
    setSession((prev) => ({ ...prev, currentStep: 3 }));
  };

  const handleBackToOverview = () => {
    setSession((prev) => ({ ...prev, currentStep: 2 }));
  };

  const handleCompetitorsConfirmed = ({ mode, competitors }) => {
    setSession((prev) => ({
      ...prev,
      competitorMode: mode,
      competitors,
      currentStep: 4, // Advance to Step 4: Positioning & Offers
    }));
  };

  const handleBackToCompetitors = () => {
    setSession((prev) => ({ ...prev, currentStep: 3 }));
  };

  const handleContinueToChannels = () => {
    setSession((prev) => ({ ...prev, currentStep: 5 }));
  };

  const handleBackToPositioning = () => {
    setSession((prev) => ({ ...prev, currentStep: 4 }));
  };

  const handleGenerateFinalReport = async () => {
    setSession((prev) => ({ ...prev, currentStep: 6 }));

    // Trigger mock competitor analysis service in background
    try {
      await analyzeCompetitors({
        businessWebsite: session.formData.business_website,
        competitors: session.competitors,
        businessOverview: session.overviewResponse?.business_overview,
      });
    } catch (err) {
      console.warn('Competitor analysis notice:', err);
    }
  };

  const handleBackToChannels = () => {
    setSession((prev) => ({ ...prev, currentStep: 5 }));
  };

  const isWideLayout = session.currentStep >= 2;
  const userBusinessName = session.overviewResponse?.business_overview?.business_name 
    || session.formData.business_name_and_description?.slice(0, 30) 
    || 'Your Business';

  return (
    <div className="app-shell">
      <TopBar
        currentStep={session.currentStep}
        onStepClick={handleStepClick}
      />

      <main className={`main-layout-viewport ${isWideLayout ? 'viewport-wide' : 'viewport-form'}`}>
        {isLoading || apiError ? (
          <Screen1Loading
            website={session.formData.business_website}
            onCancel={handleCancelLoading}
            error={apiError}
            onRetry={() => handleBusinessFormSubmit(session.formData)}
          />
        ) : session.currentStep === 1 ? (
          <Screen1BusinessForm
            initialData={session.formData}
            onSubmit={handleBusinessFormSubmit}
            isLoading={isLoading}
          />
        ) : session.currentStep === 2 ? (
          <Screen2Overview
            rawResponse={session.overviewResponse}
            onBack={handleBackToForm}
            onContinue={handleContinueToCompetitors}
          />
        ) : session.currentStep === 3 ? (
          <Screen3Competitors
            userWebsite={session.formData.business_website}
            productOrService={session.formData.product_or_service}
            businessOverview={session.overviewResponse?.business_overview}
            initialCompetitors={session.competitors}
            initialMode={session.competitorMode}
            onBack={handleBackToOverview}
            onContinue={handleCompetitorsConfirmed}
          />
        ) : session.currentStep === 4 ? (
          <Screen4PositioningOffers
            userBusinessName={userBusinessName}
            competitors={session.competitors}
            overviewData={session.overviewResponse}
            onBack={handleBackToCompetitors}
            onContinue={handleContinueToChannels}
          />
        ) : session.currentStep === 5 ? (
          <Screen5MarketingChannels
            userBusinessName={userBusinessName}
            competitors={session.competitors}
            onBack={handleBackToPositioning}
            onContinue={handleGenerateFinalReport}
          />
        ) : session.currentStep === 6 ? (
          <Screen6FinalReport
            userBusinessName={userBusinessName}
            competitors={session.competitors}
            onBack={handleBackToChannels}
          />
        ) : null}
      </main>
    </div>
  );
}
