/* ================================================================
   sw.js — Service Worker для PWA (офлайн-кэш статики)
   Версия: обновляйте CACHE_NAME при выкатке новой версии фронта
   ================================================================ */

const CACHE_NAME = 'instr-v1';

// Файлы, кэшируемые при первой установке
const PRECACHE = [
  './инструктаж.html',
  './инструктаж.css',
  './инструктаж.js',
  './login.html',
  './Ozen.png',
  './manifest.json',
];

// ─── Установка: прекэшируем статику ───────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      // addAll падает если хотя бы один файл недоступен — используем add по одному
      return Promise.allSettled(PRECACHE.map(url => cache.add(url)));
    })
  );
  // Активируем SW сразу, не ждём закрытия старых вкладок
  self.skipWaiting();
});

// ─── Активация: удаляем устаревшие кэши ────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k !== CACHE_NAME)
          .map(k => caches.delete(k))
      )
    )
  );
  // Берём контроль над уже открытыми вкладками
  self.clients.claim();
});

// ─── Fetch: стратегия ──────────────────────────────────────────────
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // API-запросы и другие origin-ы — всегда сеть, никогда не кэшируем
  if (url.pathname.startsWith('/api/') || url.origin !== self.location.origin) {
    return; // браузер делает обычный fetch
  }

  // POST/PUT/DELETE — не кэшируем
  if (request.method !== 'GET') return;

  // Статика: Cache-first со Network-fallback
  event.respondWith(
    caches.match(request).then(cached => {
      if (cached) return cached;

      return fetch(request).then(response => {
        // Кэшируем только успешные ответы
        if (response.status === 200) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(request, clone));
        }
        return response;
      }).catch(() => {
        // Офлайн и нет кэша — для HTML возвращаем главную страницу (если есть в кэше)
        if (request.headers.get('accept')?.includes('text/html')) {
          return caches.match('./инструктаж.html');
        }
      });
    })
  );
});
