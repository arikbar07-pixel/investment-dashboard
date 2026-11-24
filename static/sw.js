const CACHE = 'equityflow-v5';
const STATIC = [
  '/',
  '/static/app.js',
  '/static/style.css'
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(STATIC)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k !== CACHE).map(k => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith('/api/') || url.pathname === '/login' || url.pathname === '/logout') return;
  if (e.request.method !== 'GET') return;
  // Network-first: always serve the latest app version, fall back to cache offline
  e.respondWith(
    fetch(e.request).then(response => {
      if (response && response.ok) {
        const clone = response.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
      }
      return response;
    }).catch(() => caches.match(e.request, { ignoreSearch: true }))
  );
});
