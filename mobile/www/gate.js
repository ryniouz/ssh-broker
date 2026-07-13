/* Connectivity gate for the SSH Broker app.
 *
 * The broker web UI lives only on the home LAN. On launch we probe it
 * directly first (fastest path when you're actually home); if that fails we
 * fall back to the app's built-in WireGuard tunnel (see WgTunnelPlugin /
 * MainActivity's health check for what happens once we're past this screen).
 * If the tunnel also can't connect (no config yet, or the bundled one is no
 * longer valid), we ask for a WireGuard config file instead of just failing.
 *
 * v1.3.0: added the WireGuard fallback + config-file prompt. The retry-with-
 * backoff direct-LAN probe (v1.0.1) and the navigation-allowlist fix (v1.0.2)
 * are unchanged and still run first, since being on the home LAN needs no
 * VPN at all. */

var WEB_BASE = "http://10.11.15.11:8080";
var API_BASE = "http://10.11.15.10:8000";
var PROBES = [WEB_BASE + "/static/logo.png", API_BASE + "/health"];

var MAX_ATTEMPTS = 4;
var TIMEOUTS_MS = [2500, 3000, 4000, 5000]; // grows each retry
var RETRY_DELAY_MS = 800;

var splash = document.getElementById("splash");
var offline = document.getElementById("offline");
var configPrompt = document.getElementById("configPrompt");
var statusEl = document.getElementById("status");
var retryBtn = document.getElementById("retry");
var wgRetryBtn = document.getElementById("wgRetryBtn");
var fileInput = document.getElementById("wgFile");
var configError = document.getElementById("configError");
document.getElementById("addr").textContent = WEB_BASE.replace(/^https?:\/\//, "");

function show(el) {
  [splash, offline, configPrompt].forEach(function (s) { s.classList.add("hidden"); });
  el.classList.remove("hidden");
}

function sleep(ms) {
  return new Promise(function (resolve) { setTimeout(resolve, ms); });
}

function wgPlugin() {
  var c = window.Capacitor;
  return (c && c.Plugins && c.Plugins.WgTunnel) || null;
}

// Resolves true as soon as ANY probe URL responds (opaque no-cors success is
// enough — it only proves the host is reachable, not that auth succeeds).
function probeOnce(timeoutMs) {
  var settled = false;
  return new Promise(function (resolve) {
    var remaining = PROBES.length;
    var timers = [];
    function finish(ok) {
      if (settled) return;
      settled = true;
      timers.forEach(clearTimeout);
      resolve(ok);
    }
    PROBES.forEach(function (url) {
      var ctrl = ("AbortController" in window) ? new AbortController() : null;
      var t = setTimeout(function () { if (ctrl) ctrl.abort(); }, timeoutMs);
      timers.push(t);
      fetch(url, { mode: "no-cors", cache: "no-store", signal: ctrl ? ctrl.signal : undefined })
        .then(function () { finish(true); })
        .catch(function () {
          remaining -= 1;
          if (remaining <= 0) finish(false);
        });
    });
  });
}

async function tryDirect() {
  for (var i = 0; i < MAX_ATTEMPTS; i++) {
    statusEl.textContent = i === 0
      ? "Connecting to your home network…"
      : "Still trying (" + (i + 1) + "/" + MAX_ATTEMPTS + ")…";
    if (await probeOnce(TIMEOUTS_MS[i] || 5000)) return true;
    if (i < MAX_ATTEMPTS - 1) await sleep(RETRY_DELAY_MS);
  }
  return false;
}

async function tryVpn() {
  var plugin = wgPlugin();
  if (!plugin) return false; // running outside the native app (shouldn't happen) — no VPN available
  statusEl.textContent = "Authorizing connection…";
  try {
    await plugin.connect();
  } catch (e) {
    return false; // no config yet, bad config, or permission denied
  }
  // Tunnel reported "up", but give the handshake/routing a moment to settle
  // before trusting it, then confirm the broker is actually reachable through it.
  for (var i = 0; i < 3; i++) {
    if (await probeOnce(4000)) return true;
    await sleep(1000);
  }
  return false;
}

async function attempt() {
  show(splash);
  if (await tryDirect()) { finishToApp(); return; }
  if (await tryVpn()) { finishToApp(); return; }
  if (wgPlugin()) show(configPrompt);
  else show(offline);
}

function finishToApp() {
  statusEl.textContent = "Connected — opening dashboard…";
  window.location.replace(WEB_BASE + "/");
}

retryBtn.addEventListener("click", function () { attempt(); });
if (wgRetryBtn) wgRetryBtn.addEventListener("click", function () { attempt(); });

if (fileInput) {
  fileInput.addEventListener("change", function () {
    var file = fileInput.files && fileInput.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = async function () {
      var plugin = wgPlugin();
      if (!plugin) return;
      configError.classList.add("hidden");
      try {
        await plugin.saveConfig({ text: String(reader.result) });
        attempt();
      } catch (e) {
        configError.textContent = "That doesn't look like a valid WireGuard config file.";
        configError.classList.remove("hidden");
      }
      fileInput.value = ""; // allow re-selecting the same filename if it fails again
    };
    reader.readAsText(file);
  });
}

// First launch: give the native splash a beat, then start probing.
setTimeout(attempt, 400);
