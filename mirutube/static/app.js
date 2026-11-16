// MiruTube's page: top_bar and now_playing on every page, and between them
// home (/, or /playlist/<id> narrowed to one playlist), watch (/watch/<id>)
// or settings (/settings), each at an address of its own
// (behaviour#short-links). Positions are kept by the
// server, not the browser, so a video continues on whichever device it is
// opened next (behaviour#resume); every open page is also a device the others
// see and control (behaviour#devices).
//
// Plain modules, served as they are: each part of the page is a module of
// its own under js/, and they tell each other of changes through state.js.

import { $ } from "./js/dom.js";
import { state, setRouter, currentRoute, notify, loadCatalogue, navigate } from "./js/state.js";
import { checkSecure } from "./js/install.js";
import { closeResults, showAddress } from "./js/topbar.js";
import { showHome } from "./js/home.js";
import { showWatch, leaveWatch } from "./js/watch.js";
import { showSettings } from "./js/settings.js";
import { openEvents } from "./js/devices.js";
import { render as renderBar, closeMenus } from "./js/bar.js";
import "./js/uploads.js";

const VIEWS = ["home", "watch", "settings"];

export async function route() {
  const { view, arg } = currentRoute();
  leaveWatch();
  closeResults();
  closeMenus();
  const shown = view === "playlist" ? "home" : view;
  for (const v of VIEWS) $(v).hidden = v !== shown;
  document.body.dataset.view = shown;
  window.scrollTo(0, 0);
  notify("route");
  try {
    if (view === "watch") await showWatch(arg);
    else if (view === "settings") {
      // the bar still shows the video last watched
      if (!state.catalogue) loadCatalogue().catch((e) => console.error(e));
      await showSettings();
    }
    else await showHome(view === "playlist" ? arg : null);
  } catch (e) {
    console.error(e);
  }
  notify("route");
}

// behaviour#installable: a service worker that caches nothing, which is what
// makes the site installable where the browser counts it as secure
// (decisions#own-ca); elsewhere `serviceWorker` is not there at all.
function registerWorker() {
  if (!navigator.serviceWorker) return;
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch((e) => console.error(e));
}

// A link to one of the page's own addresses opens its view without loading
// the page again; one opened in a new tab, or with a modifier, is left to
// the browser.
document.addEventListener("click", (e) => {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const a = e.target.closest("a[href]");
  if (!a || a.target || a.hasAttribute("download")) return;
  const url = new URL(a.href, location.href);
  if (url.origin !== location.origin || url.pathname.startsWith("/static/") || url.pathname.endsWith(".crt")) return;
  e.preventDefault();
  navigate(url.pathname);
});

// an address of the earlier form, #/watch/<...>, opens home
if (location.hash.startsWith("#/")) history.replaceState(null, "", "/");

setRouter(route);
window.addEventListener("popstate", route);
checkSecure();
openEvents();
showAddress();
registerWorker();
renderBar();
route();
