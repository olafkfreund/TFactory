import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import './index.css';
import './shared/i18n';
import { initWebAPI } from './lib/api-adapter';
import { initializeGitHubListeners } from './stores/github';
import { registerWebmcpTools } from './lib/webmcp';

// Initialize web API adapter (replaces window.API)
initWebAPI();

// Initialize global GitHub event listeners (PR review progress/complete/error)
// Must be called after initWebAPI() so window.API is available
initializeGitHubListeners();

// Expose TFactory's portal actions as WebMCP tools (#333) — EXPERIMENTAL.
// Self-guarded: no-op unless VITE_WEBMCP_TOOLS=true AND the browser supports
// navigator.modelContext (Chrome 146+ behind a flag). Never throws.
try {
  registerWebmcpTools();
} catch {
  // WebMCP registration must never break portal boot.
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
