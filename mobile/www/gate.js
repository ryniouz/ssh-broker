/* Connectivity gate for the SSH Broker app.
 *
 * The broker web UI lives only on the home LAN. On launch we probe it; if it
 * answers we hand the WebView over to the live UI, otherwise we show the
 * "not on the home network" screen. No public endpoint is ever contacted. */

// Home-LAN address of the broker web UI. Local only, by design.
var BASE = "http://10.11.15.11:8080";
var PROBE = BASE + "/static/logo.png"; // small, unauthenticated, no redirect

var splash = document.getElementById("splash");
var offline = document.getElementById("offline");
var statusEl = document.getElementById("status");
var retry = document.getElementById("retry");
document.getElementById("addr").textContent = BASE.replace(/^https?:\/\//, "");

function show(el) {
  splash.classList.add("hidden");
  offline.classList.add("hidden");
  el.classList.remove("hidden");
}

function probe(timeoutMs) {
  return new Promise(function (resolve) {
    var done = false;
    var ctrl = ("AbortController" in window) ? new AbortController() : null;
    var timer = setTimeout(function () {
      if (done) return;
      done = true;
      if (ctrl) ctrl.abort();
      resolve(false);
    }, timeoutMs);
    // no-cors: an opaque success just proves the host is reachable.
    fetch(PROBE, { mode: "no-cors", cache: "no-store", signal: ctrl ? ctrl.signal : undefined })
      .then(function () { if (done) return; done = true; clearTimeout(timer); resolve(true); })
      .catch(function () { if (done) return; done = true; clearTimeout(timer); resolve(false); });
  });
}

function attempt(timeoutMs) {
  show(splash);
  statusEl.textContent = "Connecting to your home network…";
  probe(timeoutMs).then(function (ok) {
    if (ok) {
      statusEl.textContent = "Connected — opening dashboard…";
      window.location.replace(BASE + "/");
    } else {
      show(offline);
    }
  });
}

retry.addEventListener("click", function () { attempt(5000); });

// First launch: give it a moment past the native splash, then probe.
attempt(3500);
