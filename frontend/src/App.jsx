import React, { useState, useEffect, useRef } from 'react';
import {
  loadSessionState,
  saveSessionState
} from './state/sessionStore.js';
import { TopBar } from './components/common/TopBar.jsx';
import { Screen1BusinessForm } from './components/screens/Screen1BusinessForm.jsx';
import { Screen1Loading } from './components/screens/Screen1Loading.jsx';
import { Screen2Overview } from './components/screens/Screen2Overview.jsx';
import { fetchBusinessOverviewStream } from './api/client.js';
import './App.css';

export default function App() {
  const [session, setSession] = useState(() => loadSessionState());
  const [isLoading, setIsLoading] = useState(false);
  const [apiError, setApiError] = useState(null);
  const [currentStage, setCurrentStage] = useState(null);
  const [liveMeta, setLiveMeta] = useState(null);
  const [isDone, setIsDone] = useState(false);

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

  // Step 1 Submission: Stream progress and deliver Business Overview
  const handleBusinessFormSubmit = async (formData) => {
    setSession((prev) => ({ ...prev, formData }));
    setApiError(null);
    setIsLoading(true);
    setCurrentStage('visiting_website');
    setLiveMeta({ website_url: formData.business_website });
    setIsDone(false);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetchBusinessOverviewStream(
        formData,
        (evt) => {
          if (evt.stage === 'done') {
            setIsDone(true);
          } else if (evt.stage) {
            setCurrentStage(evt.stage);
            if (evt.meta) {
              setLiveMeta(evt.meta);
            }
          }
        },
        controller.signal
      );

      setIsDone(true);
      setSession((prev) => ({
        ...prev,
        overviewResponse: response,
        currentStep: 2, // Advance to Step 2: Business Overview
      }));
    } catch (err) {
      if (err.message === 'Analysis was cancelled.') {
        return;
      }

      setApiError(err.message || 'Unable to generate business overview. Please try again.');
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
    setCurrentStage(null);
    setLiveMeta(null);
    setIsDone(false);
  };

  // Navigation handlers
  const handleBackToForm = () => {
    setSession((prev) => ({ ...prev, currentStep: 1 }));
    setApiError(null);
  };

  const isWideLayout = session.currentStep >= 2;

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
            currentStage={currentStage}
            liveMeta={liveMeta}
            isDone={isDone}
          />
        ) : session.currentStep === 1 ? (
          <Screen1BusinessForm
            initialData={session.formData}
            onSubmit={handleBusinessFormSubmit}
            isLoading={isLoading}
          />
        ) : (
          <Screen2Overview
            rawResponse={session.overviewResponse}
            onBack={handleBackToForm}
          />
        )}
      </main>
    </div>
  );
}
