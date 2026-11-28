// CEM Service Worker for PWA
const CACHE_NAME = 'cem-cache-v2';
// Only immutable static assets are worth precaching. Web pages, API replies,
// attachments and notification polls must never be cloned into Cache Storage.
const urlsToCache = [
  '/static/manifest.json'
];

// Install event
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => {
        console.log('Opened cache');
        return cache.addAll(urlsToCache);
      })
  );
  self.skipWaiting();
});

// Fetch event - only same-origin /static/ GETs are cached. Everything else is
// left to the browser so huge ticket pages, photos and API replies are never
// cloned into RAM or accumulated in Cache Storage.
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  let url;
  try {
    url = new URL(event.request.url);
  } catch (e) {
    return;
  }
  if (url.origin !== self.location.origin || !url.pathname.startsWith('/static/')) return;

  // Cache by pathname, not by full URL: ?v=3/?v=4 would otherwise pile up as
  // separate entries forever in Cache Storage.
  const cacheKey = new Request(url.origin + url.pathname, { method: 'GET' });

  const network = fetch(event.request);
  // waitUntil must be registered while the event is still dispatching. It
  // clones only successful static responses and does not delay the response.
  event.waitUntil(
    network
      .then((response) => {
        if (response.status !== 200) return undefined;
        return caches.open(CACHE_NAME)
          .then((cache) => cache.put(cacheKey, response.clone()));
      })
      .catch(() => undefined)
  );
  event.respondWith(network.catch(() => caches.match(cacheKey)));
});

// Activate event - Clean up old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
  self.clients.claim();
});

// Push notification event
self.addEventListener('push', (event) => {
  console.log('Push notification received:', event);
  
  let data = {
    title: 'CEM Notification',
    body: 'You have a new notification',
    icon: '/static/icons/icon-192x192.png',
    badge: '/static/icons/icon-72x72.png',
    url: '/web/dashboard'
  };
  
  if (event.data) {
    try {
      data = event.data.json();
    } catch (e) {
      console.error('Error parsing push data:', e);
    }
  }
  
  const options = {
    body: data.body,
    icon: data.icon,
    badge: data.badge,
    vibrate: [200, 100, 200],
    data: {
      url: data.url || '/web/dashboard',
      dateOfArrival: Date.now()
    },
    actions: [
      {
        action: 'open',
        title: 'Open'
      },
      {
        action: 'close',
        title: 'Close'
      }
    ],
    tag: data.tag || 'default-notification',
    requireInteraction: false
  };
  
  event.waitUntil(
    self.registration.showNotification(data.title, options)
  );
});

// Notification click event
self.addEventListener('notificationclick', (event) => {
  console.log('Notification clicked:', event);
  
  event.notification.close();
  
  if (event.action === 'close') {
    return;
  }
  
  const urlToOpen = event.notification.data.url || '/web/dashboard';
  
  event.waitUntil(
    clients.matchAll({
      type: 'window',
      includeUncontrolled: true
    }).then((clientList) => {
      // Check if there's already a window open
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          client.focus();
          client.navigate(urlToOpen);
          return;
        }
      }
      // No window open, open a new one
      if (clients.openWindow) {
        return clients.openWindow(urlToOpen);
      }
    })
  );
});
