// Installing MiruTube as an app (behaviour#installable) and HTTPS on the home
// network (behaviour#secure-at-home): install_button, install_card, the
// settings section that opens it, and the quiet move to HTTPS of a device
// that trusts MiruTube's certificate.

import { $, el } from "./dom.js";

const PROBE_TIMEOUT = 3000; // ms the HTTPS probe may take

const httpsHref = () => "https://" + location.host + location.pathname + location.search;

// A browser counts these as secure over plain HTTP too.
function local() {
  const h = location.hostname;
  return h === "localhost" || h === "127.0.0.1" || h === "[::1]" || h === "::1";
}

const secure = () => location.protocol === "https:" || local();
const installed = () => window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

// --------------------------------------------------------------- the probe

// Whether this device trusts MiruTube's certificate: a request to the same
// server over HTTPS goes through only if it does.
async function trusted() {
  const stop = new AbortController();
  const timer = setTimeout(() => stop.abort(), PROBE_TIMEOUT);
  try {
    await fetch("https://" + location.host + "/api/ping", { mode: "no-cors", cache: "no-store", signal: stop.signal });
    return true;
  } catch (e) {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

// On a page over plain HTTP at the network address: the same view over HTTPS
// when this device trusts the certificate; nothing shown when it does not.
export async function checkSecure() {
  renderSettings();
  showButton();
  if (secure()) return;
  if (await trusted()) location.replace(httpsHref());
}

// ---------------------------------------------------------- install_button

// There until MiruTube is installed on this device: the browser's own dialog
// where it offers one (Chromium-based browsers say so to the page), else
// install_card.
let installPrompt = null;

function showButton() {
  $("install-button").hidden = installed();
}

window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installPrompt = e;
  showButton();
});
window.addEventListener("appinstalled", () => {
  installPrompt = null;
  $("install-button").hidden = true;
  closeCard();
});
$("install-button").addEventListener("click", async () => {
  if (!installPrompt) return openCard();
  const offered = installPrompt;
  installPrompt = null;
  await offered.prompt();
});

// ------------------------------------------------------------ install_card

// The platform and browser this page runs in.
function platform() {
  const ua = navigator.userAgent;
  if (/Android/i.test(ua)) return /Firefox\//.test(ua) ? "android-firefox" : "android";
  if (/iPhone|iPad|iPod/i.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return "ios";
  if (/Firefox\//.test(ua)) return "firefox";
  if (/Macintosh/i.test(ua) && /Safari\//.test(ua) && !/Chrome\/|Chromium\//.test(ua)) return "safari";
  if (/Edg\//.test(ua)) return "edge";
  return "chromium";
}

// Installing the certificate, per system: the browser decides only on Linux.
const CERTIFICATE = {
  android: ["Android", [
    "Press <b>Download certificate</b> above.",
    "Open <b>Settings → Security and privacy → More security settings → Encryption and credentials → Install a certificate → CA certificate</b>, press <b>Install anyway</b> and choose <code>MiruTube.crt</code>.",
    "Come back and press <b>Open over HTTPS</b>.",
  ]],
  ios: ["iPhone and iPad", [
    "Press <b>Download certificate</b> above in Safari and allow the profile to download.",
    "Open <b>Settings → Profile Downloaded → Install</b>.",
    "Open <b>Settings → General → About → Certificate Trust Settings</b> and turn on <b>MiruTube on …</b>.",
    "Come back and press <b>Open over HTTPS</b>.",
  ]],
  windows: ["Windows — Chrome and Edge", [
    "Press <b>Download certificate</b> above and open the file.",
    "Press <b>Install Certificate</b>, choose <b>Current User</b>, then <b>Place all certificates in the following store → Browse → Trusted Root Certification Authorities</b>, and finish; answer <b>Yes</b> to the warning.",
    "Restart the browser and press <b>Open over HTTPS</b>.",
  ]],
  mac: ["macOS — Safari and Chrome", [
    "Press <b>Download certificate</b> above and open the file: Keychain Access adds it.",
    "In Keychain Access double-click <b>MiruTube on …</b>, open <b>Trust</b> and set <b>When using this certificate</b> to <b>Always Trust</b>.",
    "Press <b>Open over HTTPS</b>.",
  ]],
  linux: ["Linux — Chrome and Chromium", [
    "Press <b>Download certificate</b> above.",
    "Open <code>chrome://certificate-manager</code>, go to <b>Local certificates → Installed by you</b>, and import <code>MiruTube.crt</code> under <b>Trusted certificates</b>.",
    "Press <b>Open over HTTPS</b>.",
  ]],
  firefox: ["Firefox", [
    "Press <b>Download certificate</b> above.",
    "Open <b>Settings → Privacy &amp; Security → Certificates → View Certificates → Authorities → Import</b>, choose <code>MiruTube.crt</code> and tick <b>Trust this CA to identify websites</b>.",
    "Press <b>Open over HTTPS</b>.",
  ]],
};

function certificateKey() {
  const ua = navigator.userAgent;
  if (/Android/i.test(ua)) return "android";
  if (platform() === "ios") return "ios";
  if (/Firefox\//.test(ua)) return "firefox";
  if (/Windows/i.test(ua)) return "windows";
  if (/Macintosh/i.test(ua)) return "mac";
  return "linux";
}

// The step that installs, per browser, once the page is secure.
const INSTALL = {
  android: "Open the browser menu <b>⋮ → Add to Home screen → Install</b>.",
  "android-firefox": "Open the browser menu <b>⋮ → Add app to Home screen</b>.",
  ios: "In Safari press <b>Share → Add to Home Screen</b>.",
  safari: "In Safari choose <b>File → Add to Dock</b>.",
  firefox: "Firefox does not install sites as apps on a computer. Open this address in Chrome, Edge or another "
    + "Chromium-based browser and install it from there.",
  edge: "Press the install icon at the right end of the address bar, or open the menu <b>… → Apps → Install this site as an app</b>. "
    + "If MiruTube is installed already, Edge offers to open it in the app instead.",
  chromium: "Press the install icon at the right end of the address bar, or open the menu <b>⋮ → Cast, save and share → Install page</b>. "
    + "If MiruTube is installed already, the browser offers to open it in the app instead.",
};

function list(items) {
  const ol = el("ol");
  ol.innerHTML = items.map((s) => "<li>" + s + "</li>").join("");
  return ol;
}

function certificateSteps(key) {
  const [title, items] = CERTIFICATE[key];
  return [el("h4", { text: title }), list(items)];
}

export function openCard() {
  const needsHttps = !secure();
  $("install-https").hidden = !needsHttps;
  $("install-number").hidden = !needsHttps;
  if (needsHttps) {
    const mine = certificateKey();
    $("certificate-steps").replaceChildren(...certificateSteps(mine));
    $("certificate-other-steps").replaceChildren(
      ...Object.keys(CERTIFICATE).filter((k) => k !== mine).flatMap(certificateSteps));
    $("certificate-other").open = false;
  }
  const step = el("p", { class: "install-how" });
  step.innerHTML = (needsHttps ? "Once MiruTube is open over HTTPS: " : "") + INSTALL[platform()];
  $("install-steps").replaceChildren(step);
  $("install-card").hidden = false;
  $("install-close").focus();
}

function closeCard() {
  $("install-card").hidden = true;
}

$("install-close").addEventListener("click", closeCard);
$("install-card").addEventListener("click", (e) => {
  if (e.target === $("install-card")) closeCard();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("install-card").hidden) closeCard();
});
$("certificate-open").addEventListener("click", () => location.assign(httpsHref()));

// ------------------------------------------------------------- settings

function renderSettings() {
  $("install-state").textContent = installed()
    ? "MiruTube runs here as an installed app."
    : location.protocol === "https:"
      ? "This device opens MiruTube over HTTPS, so it can be installed here as an app."
      : local()
        ? "This is the computer MiruTube runs on: it can be installed here as an app without a certificate."
        : "This device opens MiruTube over plain HTTP. To install it as an app, install MiruTube’s certificate first.";
}
$("install-open").addEventListener("click", openCard);
