/* Connectivity gate for the SSH Broker app.
 *
 * The broker web UI lives only on the home LAN. On launch we probe it; if it
 * answers we hand the WebView over to the live UI, otherwise we show the
 * "not on the home network" screen. No public endpoint is ever contacted.
 *
 * v1.2.5: the very first probe after a cold start can legitimately fail even
 * ON the home network — the phone's Wi-Fi radio may still be waking from a
 * low-power state, and this LAN's macvlan-attached containers have a known
 * "first request after idle" ARP-resolution delay (same class of issue
 * documented for other services on this network). A single 3.5s attempt was
 * declaring "offline" on what was really just a slow first packet. Now we
 * retry automatically with backoff, and probe two independent endpoints so a
 * single slow service doesn't fail the whole check. */

// Home-LAN addresses of the broker. Local only, by design.
var WEB_BASE = "http://10.11.15.11:8080";
var API_BASE = "http://10.11.15.10:8000";
var PROBES = [WEB_BASE + "/static/logo.png", API_BASE + "/health"];

var MAX_ATTEMPTS = 4;
var TIMEOUTS_MS = [2500, 3000, 4000, 5000]; // grows each retry
var RETRY_DELAY_MS = 800;

var splash = document.getElementById("splash");
var offline = document.getElementById("offline");
var statusEl = document.getElementById("status");
var retryBtn = document.getElementById("retry");
document.getElementById("addr").textContent = WEB_BASE.replace(/^https?:\/\//, "");

function show(el) {
  splash.classList.add("hidden");
  offline.classList.add("hidden");
  el.classList.remove("hidden");
}

function sleep(ms) {
  return new Promise(function (resolve) { setTimeout(resolve, ms); });
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

async function attempt() {
  show(splash);
  for (var i = 0; i < MAX_ATTEMPTS; i++) {
    statusEl.textContent = i === 0
      ? "Connecting to your home network…"
      : "Still trying (" + (i + 1) + "/" + MAX_ATTEMPTS + ")…";
    var ok = await probeOnce(TIMEOUTS_MS[i] || 5000);
    if (ok) {
      statusEl.textContent = "Connected — opening dashboard…";
      window.location.replace(WEB_BASE + "/");
      return;
    }
    if (i < MAX_ATTEMPTS - 1) await sleep(RETRY_DELAY_MS);
  }
  show(offline);
}

retryBtn.addEventListener("click", function () { attempt(); });

// First launch: give the native splash a beat, then start probing.
setTimeout(attempt, 400);
