// Deliberately minimal: installability (Chrome's "Add to Home Screen"
// prompt) requires an active service worker with a fetch handler, but
// this dashboard shows live trading/halt/position state -- caching any
// of that would risk showing stale data after a real trade or halt.
// This is a pure network passthrough, no caching, no offline mode.
self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});
