// Service worker mínimo: solo habilita la instalación como PWA.
// No cachea nada a propósito — los datos del panel cambian todo
// el tiempo y servir una copia vieja sería peor que no tener SW.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
