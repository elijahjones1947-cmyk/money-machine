// Thin fetch wrapper around the Flask JSON API. Every call sends the
// session cookie (credentials: 'include') since auth is Flask-session
// based, not token based — same origin, so no CORS/token juggling needed.

async function request(path, options = {}) {
  const res = await fetch(path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  let body = null;
  try {
    body = await res.json();
  } catch {
    // non-JSON response (shouldn't normally happen against /api or /ui)
  }
  if (!res.ok) {
    const message = (body && body.error) || `Request failed (${res.status})`;
    const err = new Error(message);
    err.status = res.status;
    throw err;
  }
  return body;
}

export const api = {
  login: (password) =>
    request('/api/login', { method: 'POST', body: JSON.stringify({ password }) }),
  logout: () => request('/api/logout', { method: 'POST' }),
  session: () => request('/api/session'),

  dashboard: () => request('/api/dashboard'),

  toggleBot: () => request('/api/toggle_bot', { method: 'POST' }),
  updateSettings: (payload) =>
    request('/api/settings', { method: 'POST', body: JSON.stringify(payload) }),
  addWatchlist: (symbol, assetClass) =>
    request('/api/watchlist', {
      method: 'POST',
      body: JSON.stringify({ symbol, asset_class: assetClass }),
    }),
  manualTrade: (action, symbol) =>
    request('/api/manual_trade', {
      method: 'POST',
      body: JSON.stringify({ action, symbol }),
    }),

  backtest: () => request('/api/backtest'),

  hermesHistory: () => request('/api/hermes/history'),
  hermesChat: (message) =>
    request('/api/hermes/chat', { method: 'POST', body: JSON.stringify({ message }) }),
  hermesConfirm: (confirm) =>
    request('/api/hermes/confirm', { method: 'POST', body: JSON.stringify({ confirm }) }),

  getLayout: () => request('/ui/layout'),
  saveLayout: (layout) =>
    request('/ui/layout', { method: 'POST', body: JSON.stringify(layout) }),
};
