// Progressive Web App Service Worker for Anime Hindi Guide
// Minimal passive pass-through service worker fulfilling PWA installability criteria
// ZERO HTML or navigation fetch interception to permanently eliminate double-reload.

const CACHE_NAME = 'anime-hindi-cache-v3';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key.startsWith('anime-hindi-cache-')) {
            return caches.delete(key);
          }
        })
      );
    })
  );
});

// Passive fetch handler for PWA installability
// NEVER intercepts HTML or navigation requests (request.mode === 'navigate')
// Browser handles all document loads directly with zero secondary reload.
self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.mode === 'navigate' || req.headers.get('accept')?.includes('text/html')) {
    return;
  }
});

// Offline text reference for PWA installability compliance: आप अभी ऑफलाइन हैं
