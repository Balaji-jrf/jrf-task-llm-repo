import React from 'react';
import { createRoot } from 'react-dom/client';
import LLMTab from './components/LLMTab';

const apiUrl = import.meta.env.VITE_JRF_TASK_LLM_API_URL || '';
const ocrApiUrl = import.meta.env.VITE_OCR_API_URL
  || 'https://o0dqnexpsg.execute-api.ap-southeast-2.amazonaws.com/prod';
const isLocalDevelopmentApi = import.meta.env.DEV
  && /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?\/api\/?$/.test(apiUrl);

function App() {
  if (!apiUrl || (!apiUrl.startsWith('https://') && !isLocalDevelopmentApi)) {
    return (
      <main className="jrf-llm-workspace">
        <p className="llm-error" role="alert">
          The HTTPS Qwen API URL is not configured. Set VITE_JRF_TASK_LLM_API_URL in the Amplify branch environment.
        </p>
      </main>
    );
  }

  return (
    <LLMTab
      apiUrl={apiUrl}
      ocrApiUrl={ocrApiUrl}
    />
  );
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
