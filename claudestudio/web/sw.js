/* ClaudeStudio service worker (Feature 2.5, v0.6.0).
 *
 * Caches the static app shell (HTML/CSS/JS/icons) so the UI loads instantly —
 * even before the Python server is up — and shows an offline state with a retry
 * when both cache and network miss. API responses are NEVER cached: data is
 * always fetched network-first so the index is never stale. When a new shell is
 * deployed the page is told to show an "Update available — reload" toast.
 *
 * 100% local: the worker only ever touches same-origin requests; it makes no
 * outbound calls of its own.
 */

const CACHE = 'claudestudio-shell-v0.6.0';
const SHELL = [
  './',
  './index.html',
  './styles.css',
  './app.js',
  './keyboard.js',
  './manifest.json',
  './assets/icon.svg',
  './assets/icon-192.svg',
  './assets/icon-512.svg',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
      .then(() => notifyClients({ type: 'sw-updated' }))
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  // Only handle our own origin; let everything else pass through untouched.
  if (url.origin !== self.location.origin || req.method !== 'GET') return;
  // API + SSE: always network-first, never cached (data must be live).
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(req));
    return;
  }
  // Static shell: cache-first for instant loads, with a network refresh; fall
  // back to a minimal offline page + retry when both miss.
  event.respondWith(
    caches.match(req).then((cached) => {
      const fetched = fetch(req)
        .then((res) => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => cached || offlineResponse());
      return cached || fetched;
    })
  );
});

function offlineResponse() {
  const html =
    '<!doctype html><meta charset="utf-8"><title>ClaudeStudio — offline</title>' +
    '<style>body{font-family:system-ui;background:#0f1014;color:#e8e8ec;display:grid;' +
    'place-items:center;height:100vh;margin:0}button{margin-top:14px;padding:8px 16px;' +
    'border-radius:8px;border:0;background:#9a8cff;color:#111;font-weight:600;cursor:pointer}</style>' +
    '<div style="text-align:center"><h1>Server offline</h1>' +
    '<p>The ClaudeStudio server isn\'t running yet.</p>' +
    '<button onclick="location.reload()">Retry</button></div>';
  return new Response(html, { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
}

function notifyClients(msg) {
  return self.clients.matchAll().then((cs) => cs.forEach((c) => c.postMessage(msg)));
}
