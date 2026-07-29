/* tl-dashboards service worker — PWA offline shell (Phase 1, 2026.07.29).
   Lives at the site ROOT (dist/sw.js), scope = whole site; every page
   registers it as ../sw.js. Strategy:
   - pages (navigations): NETWORK-FIRST — data refreshes daily, fresh wins
     when online; offline falls back to the last cached copy. Each page is
     fully self-contained (data embedded, encrypted), so one cached HTML
     = a working offline dashboard (unlock works offline too — the
     remembered-passphrase key lives in IndexedDB on the device).
   - static assets (icons, manifest): cache-first, refreshed in background.
   One versioned cache; old versions pruned on activate. */
const C = 'tlk-v1';

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', e => e.waitUntil(
  caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== C).map(k => caches.delete(k))))
    .then(() => self.clients.claim())
));

self.addEventListener('fetch', e => {
  const r = e.request;
  if (r.method !== 'GET' || new URL(r.url).origin !== location.origin) return;

  // Cache-write rules that all bit us in testing: clone() SYNCHRONOUSLY
  // (inside .then the body is already streamed to the page → clone throws),
  // key by URL not Request (put() rejects mode-'navigate' requests), and
  // e.waitUntil so the SW isn't killed with the put still pending.
  const store = res => {
    if (res.ok) {
      const cp = res.clone();
      e.waitUntil(caches.open(C).then(c => c.put(r.url, cp)));
    }
    return res;
  };

  if (r.mode === 'navigate' || r.destination === 'document') {
    e.respondWith(
      fetch(r).then(store).catch(() => caches.match(r, { ignoreSearch: true }))
    );
    return;
  }

  e.respondWith(
    caches.match(r).then(hit => {
      const net = fetch(r).then(store).catch(() => hit);
      return hit || net;
    })
  );
});
