// PWA Install Prompt Handler — floating install button disabled.
// The beforeinstallprompt event is suppressed so no install UI appears.
// Service worker (sw.js) is still registered for caching/offline support.

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault(); // suppress browser's native install prompt
});
