// Progressive Web App Service Worker for Anime Hindi Guide
const CACHE_NAME = 'anime-hindi-cache-v1';
const PRECACHE_URLS = [
  '/',
  '/free',
  '/manifest.webmanifest',
  '/static/favicon.svg',
  '/static/css/tailwind.css',
  '/icons/icon-192.png',
  '/icons/icon-512.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(PRECACHE_URLS).catch((err) => {
        console.warn('SW pre-cache non-fatal warning:', err);
      });
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Skip non-GET requests and cross-origin requests
  if (req.method !== 'GET' || url.origin !== self.location.origin) {
    return;
  }

  // Network-first strategy for HTML pages: live updates/auth/theme/language NEVER stuck in stale cache!
  if (req.mode === 'navigate' || req.headers.get('accept')?.includes('text/html')) {
    event.respondWith(
      fetch(req)
        .then((response) => {
          return response;
        })
        .catch(() => {
          return caches.match(req).then((cached) => {
            if (cached) return cached;
            return new Response(
              `<!DOCTYPE html>
<html lang="hi" class="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ऑफलाइन — एनीमे हिंदी गाइड</title>
  <style>
    body { margin: 0; background: #09090b; color: #f4f4f5; font-family: system-ui, -apple-system, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; padding: 24px; text-align: center; }
    .card { max-width: 440px; background: #18181b; border: 1px solid #27272a; border-radius: 20px; padding: 36px 28px; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.6); }
    .icon { font-size: 48px; margin-bottom: 16px; display: inline-block; }
    h1 { font-size: 22px; color: #ef4444; margin: 0 0 12px 0; font-weight: 800; }
    p { font-size: 14px; color: #a1a1aa; line-height: 1.6; margin: 0 0 24px 0; }
    button { background: #dc2626; color: white; border: none; padding: 12px 24px; font-weight: 700; font-size: 14px; border-radius: 12px; cursor: pointer; transition: background 0.2s; }
    button:hover { background: #b91c1c; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">📡</div>
    <h1>आप अभी ऑफलाइन हैं</h1>
    <p>कृपया अपना इंटरनेट कनेक्शन जांचें। जैसे ही नेटवर्क उपलब्ध होगा, ताज़ा एनीमे अपडेट्स, शेड्यूल्स और स्ट्रीमिंग गाइड्स तुरंत लोड हो जाएंगे।</p>
    <button onclick="window.location.reload()">फिर से लोड करें</button>
  </div>
</body>
</html>`,
              {
                headers: { 'Content-Type': 'text/html; charset=utf-8' },
                status: 200
              }
            );
          });
        })
    );
    return;
  }

  // Cache-first for static CSS, fonts, and icons
  if (url.pathname.startsWith('/static/') || url.pathname.startsWith('/icons/')) {
    event.respondWith(
      caches.match(req).then((cached) => {
        if (cached) return cached;
        return fetch(req).then((networkRes) => {
          if (networkRes && networkRes.status === 200) {
            const clone = networkRes.clone();
            caches.open(CACHE_NAME).then((c) => c.put(req, clone));
          }
          return networkRes;
        });
      })
    );
    return;
  }

  // Default network fetch
  event.respondWith(fetch(req));
});
