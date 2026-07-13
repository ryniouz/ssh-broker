/* Connectivity gate for the SSH Broker app.
 *
 * On launch we decide the fastest sensible path:
 *   - On the home Wi-Fi ("Lucas Network"), or if we can't determine the
 *     network, try the broker directly on the LAN first (with retries), and
 *     only fall back to the VPN if that fails.
 *   - On cellular or any OTHER Wi-Fi, the LAN is unreachable by definition, so
 *     go straight to the built-in WireGuard tunnel (skipping the doomed LAN
 *     retries) -- unless the user cancels.
 * If the tunnel can't connect (no config yet / no longer valid), we ask for a
 * WireGuard config file instead of just failing.
 *
 * v1.3.3: added Wi-Fi/cellular detection + a cancel button during VPN auth.
 * The direct-LAN retry probe (v1.0.1) and navigation-allowlist fix (v1.0.2)
 * are unchanged and still run whenever we take the direct path. */

var HOME_SSID = "Lucas Network";
var WEB_BASE = "http://10.11.15.11:8080";
var API_BASE = "http://10.11.15.10:8000";
var PROBES = [WEB_BASE + "/static/logo.png", API_BASE + "/health"];

var MAX_ATTEMPTS = 4;
var TIMEOUTS_MS = [2500, 3000, 4000, 5000];
var RETRY_DELAY_MS = 800;

var splash = document.getElementById("splash");
var offline = document.getElementById("offline");
var configPrompt = document.getElementById("configPrompt");
var statusEl = document.getElementById("status");
var retryBtn = document.getElementById("retry");
var wgRetryBtn = document.getElementById("wgRetryBtn");
var fileInput = document.getElementById("wgFile");
var configError = document.getElementById("configError");
var cancelBtn = document.getElementById("vpnCancel");
document.getElementById("addr").textContent = WEB_BASE.replace(/^https?:\/\//, "");

var canceled = false;

function show(el) {
  [splash, offline, configPrompt].forEach(function (s) { s.classList.add("hidden"); });
  el.classList.remove("hidden");
}
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
function wgPlugin() {
  var c = window.Capacitor;
  return (c && c.Plugins && c.Plugins.WgTunnel) || null;
}

function probeOnce(timeoutMs) {
  var settled = false;
  return new Promise(function (resolve) {
    var remaining = PROBES.length;
    var timers = [];
    function finish(ok) {
      if (settled) return;
      settled = true; timers.forEach(clearTimeout); resolve(ok);
    }
    PROBES.forEach(function (url) {
      var ctrl = ("AbortController" in window) ? new AbortController() : null;
      var t = setTimeout(function () { if (ctrl) ctrl.abort(); }, timeoutMs);
      timers.push(t);
      fetch(url, { mode: "no-cors", cache: "no-store", signal: ctrl ? ctrl.signal : undefined })
        .then(function () { finish(true); })
        .catch(function () { remaining -= 1; if (remaining <= 0) finish(false); });
    });
  });
}

async function getNetwork() {
  var p = wgPlugin();
  if (!p || !p.networkInfo) return { type: "unknown", ssid: null };
  try { return await p.networkInfo(); } catch (e) { return { type: "unknown", ssid: null }; }
}

async function tryDirect() {
  for (var i = 0; i < MAX_ATTEMPTS; i++) {
    if (canceled) return false;
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
  if (!plugin) return false;
  statusEl.textContent = "Authorizing connection…";
  cancelBtn.classList.remove("hidden");
  try {
    await plugin.connect();
  } catch (e) {
    cancelBtn.classList.add("hidden");
    return false;
  }
  for (var i = 0; i < 4; i++) {
    if (canceled) { cancelBtn.classList.add("hidden"); return false; }
    if (await probeOnce(4000)) { cancelBtn.classList.add("hidden"); return true; }
    await sleep(1000);
  }
  cancelBtn.classList.add("hidden");
  return false;
}

async function attempt() {
  canceled = false;
  cancelBtn.classList.add("hidden");
  show(splash);

  var net = await getNetwork();
  // Home Wi-Fi, or a network we can't identify -> the LAN might be reachable,
  // so try it directly first. Cellular / another Wi-Fi -> LAN is hopeless, VPN.
  var tryLanFirst =
    (net.type === "wifi" && (net.ssid === HOME_SSID || !net.ssid)) ||
    net.type === "unknown" || net.type === "none";

  if (tryLanFirst && await tryDirect()) { finishToApp(); return; }
  if (canceled) { show(offline); return; }

  if (await tryVpn()) { finishToApp(); return; }

  if (canceled) { show(offline); return; }
  if (wgPlugin()) show(configPrompt); else show(offline);
}

function finishToApp() {
  cancelBtn.classList.add("hidden");
  statusEl.textContent = "Connected — opening dashboard…";
  window.location.replace(WEB_BASE + "/");
}

retryBtn.addEventListener("click", function () { attempt(); });
if (wgRetryBtn) wgRetryBtn.addEventListener("click", function () { attempt(); });
if (cancelBtn) cancelBtn.addEventListener("click", function () {
  canceled = true;
  var p = wgPlugin();
  if (p && p.disconnect) { try { p.disconnect(); } catch (e) {} }
  cancelBtn.classList.add("hidden");
  show(offline);
});

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
      fileInput.value = "";
    };
    reader.readAsText(file);
  });
}

setTimeout(attempt, 400);
