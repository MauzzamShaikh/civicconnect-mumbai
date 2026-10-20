const CACHE = 'civicsocial-shell-v5';
const SHELL = ['./', './index.html', './styles.css', './app.js', './post-submission.mjs', './manifest.webmanifest', './icon.svg'];
const API_PATHS = ['/api', '/auth', '/users', '/posts', '/notifications', '/health'];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(Promise.all([
    caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('civicsocial-shell-') && key !== CACHE).map(key => caches.delete(key)))),
    self.clients.claim(),
  ]));
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  if (request.headers.has('authorization')) return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (API_PATHS.some(path => url.pathname === path || url.pathname.startsWith(`${path}/`))) return;

  // Only cache the app shell. Network-first requests pick up deployed asset
  // changes without relying on an API response or manual cache invalidation.
  const isShellAsset = SHELL.some(path => new URL(path, self.location.href).pathname === url.pathname);
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(() => caches.match('./index.html')));
  } else if (isShellAsset) {
    event.respondWith(fetch(request).then(async response => {
      if (response.ok) {
        // Clone while the response body is still untouched. Cloning from a
        // later caches.open() callback races the browser consuming this body.
        const cacheCopy = response.clone();
        try {
          const cache = await caches.open(CACHE);
          await cache.put(request, cacheCopy);
        } catch {
          // A cache write failure must not prevent the network asset loading.
        }
      }
      return response;
    }).catch(() => caches.match(request)));
  }
});
