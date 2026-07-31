import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.jsx';
import './styles/themes.css';
import './styles/global.css';

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// Registered for installability only (see service-worker.js's own
// comment) -- not gated behind a production check since this app has
// no separate dev-server PWA testing path and the worker itself is a
// pure passthrough, safe to run under `npm run dev` too.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js').catch(() => {});
  });
}
