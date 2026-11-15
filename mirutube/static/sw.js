// MiruTube's service worker. It is here only because a browser installs a
// site as an app when one is registered; it caches nothing and answers
// nothing itself, since MiruTube without its server has nothing to show
// (decisions#install-secure). Every request goes to the network as usual.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
