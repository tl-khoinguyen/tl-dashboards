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

/* Web Push (Phase 2) — payload: {title, body, url}. Sent by GitHub Actions
   after a successful scheduled build (pywebpush + VAPID). */
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) {}
  e.waitUntil(self.registration.showNotification(d.title || 'TLダッシュボード', {
    body: d.body || 'ダッシュボード更新 / Dashboard updated',
    icon: 'icon-192.png', badge: 'badge-96.png',  // badge MUST be monochrome — else Android falls back to the Chrome logo
    // tag (08.07): collapse duplicates — one device can end up holding more than
    // one live subscription (scope split, endpoint rotation), and without a tag
    // each delivery stacks as its own notification. renotify keeps the alert.
    tag: 'tlk-update', renotify: true,
    data: { url: d.url || self.registration.scope },
  }));
});

/* pushsubscriptionchange (08.06): Chrome/Android rotates or silently revokes FCM
   subscriptions (Doze/battery management — three dead subs in four days). When the
   browser fires this event, re-subscribe and re-register with the worker using the
   config the page saved at subscribe time (IndexedDB tlk-push/cfg) — so a rotation
   heals itself without a page open. */
self.addEventListener('pushsubscriptionchange', e => {
  e.waitUntil((async () => {
    const cfg = await new Promise(res => {
      try {
        const r = indexedDB.open('tlk-push', 1);
        r.onupgradeneeded = () => r.result.createObjectStore('cfg');
        r.onsuccess = () => { try {
          const g = r.result.transaction('cfg', 'readonly').objectStore('cfg').get('push');
          g.onsuccess = () => res(g.result); g.onerror = () => res(null);
        } catch (_) { res(null); } };
        r.onerror = () => res(null);
      } catch (_) { res(null); }
    });
    if (!cfg) return;
    const u8 = b64 => { const s = atob(b64.replace(/-/g, '+').replace(/_/g, '/')); const a = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) a[i] = s.charCodeAt(i); return a; };
    try {
      const sub = e.newSubscription || await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: u8(cfg.pub) });
      await fetch(cfg.url + '/sub', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ v: cfg.v, sub: sub.toJSON() }) });
      // drop the rotated-away record (08.07) — the registry keys by endpoint, so
      // without this the old one lingers until the push service finally 410s it
      // and the device receives the same ping twice.
      const old = e.oldSubscription && e.oldSubscription.endpoint;
      if (old) await fetch(cfg.url + '/sub', { method: 'DELETE', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ v: cfg.v, endpoint: old }) });
    } catch (_) {}
  })());
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || self.registration.scope;
  e.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then(ws => {
    for (const w of ws) if (w.url.startsWith(self.registration.scope) && 'focus' in w) return w.focus();
    return clients.openWindow(url);
  }));
});

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
