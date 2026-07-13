# SSH Broker — Android app

A thin **hybrid (Capacitor) WebView** shell for the SSH Broker dashboard. It's
intentionally **home-LAN only**: the broker web UI is never exposed to the
public internet, so the app normally talks only to the local address
`http://10.11.15.11:8080` — and falls back to a built-in **WireGuard tunnel**
when you're away from home.

## How it behaves
1. **Splash** (dark, terminal-green logo) on launch.
2. **Connectivity gate**, in order:
   - **Network-aware start (v1.3.3):** if you're on the home Wi-Fi
     (`Lucas Network`) — or the app can't identify the network — it tries the
     broker directly on the LAN first. On cellular or any *other* Wi-Fi the LAN
     is unreachable by definition, so it skips straight to the VPN.
   - Direct LAN probe (with retries) when applicable.
   - Not reachable → brings up the built-in **WireGuard tunnel** (system VPN
     consent prompt the first time), shows **"Authorizing connection…"** with a
     **Cancel** option, then re-probes the broker through the tunnel.
   - Tunnel also can't connect (no config yet, or it's no longer valid) → asks
     you to **choose a WireGuard config file**, validates and saves it, then
     retries.
   - Still nothing → a clean **"You're not on the home network"** screen with
     a retry button, instead of a browser error.
3. Once connected, login and everything else are served by the broker's own
   web UI. An **"Exit & VPN off"** item in the menu closes the app *and* tears
   down the device VPN so it doesn't linger (the tunnel is device-wide).
4. **While using the app**, a native background check (not the JS above —
   that only runs once at launch) watches for the connection dropping. If it
   does, the same **"Authorizing connection…"** look appears over whatever
   page you're on, and it tries a silent VPN reconnect (only if consent was
   already granted) before handing control back.

Native niceties (v1.3.3): the system status/navigation bars are coloured to
match the app's dark top chrome, the WebView's rubber-band overscroll bounce is
disabled, and the app clears its WebView cache on every launch.

The home Wi-Fi SSID name is read via `ACCESS_FINE_LOCATION` (Android requires
location permission to see the SSID). If you decline it, the app degrades
gracefully to transport-only logic: any Wi-Fi → try LAN then VPN; cellular →
VPN.

## Layout
```
mobile/
├── capacitor.config.json   # appId com.ryniouz.sshbroker, dark splash/status bar,
│                           #   server.allowNavigation for the broker's LAN IPs
├── www/                    # the bundled gate: index.html, gate.js, styles.css, logo.png
├── native-src/              # custom Java, copied into the generated android/ project
│   ├── MainActivity.java    #   registers WgTunnel plugin + the drop-detection watchdog/overlay
│   ├── WgTunnelPlugin.java  #   Capacitor JS bridge: connect/disconnect/status/saveConfig
│   └── WgTunnelManager.java #   wraps the WireGuard-Android library (GoBackend/Tunnel/Config)
├── resources/icon-src.png  # the app icon (source)
├── android-res/network_security_config.xml   # cleartext allowed for LAN IPs only
├── secrets/default.conf    # YOUR WireGuard config, bundled into the APK as the
│                           #   default tunnel — gitignored, THIS MACHINE ONLY, never in git
├── scripts/gen-assets.mjs  # makes 1024 icon + 2732 splash from the source icon
└── build.sh                # one-shot build -> signed app-release.apk
```
The generated `android/`, `node_modules/`, keystore, APK, and `secrets/` are
all git-ignored. `secrets/default.conf` in particular holds a real WireGuard
private key — it's read locally by `build.sh` and baked into the APK as an
asset, but the file itself never touches GitHub.

## Build
Requires Node, JDK 17, and the Android SDK at `C:\Android`. Build **outside**
OneDrive (its sync breaks gradle) — `build.sh` handles that. Pass the version
to stamp into the APK (defaults shown):

```bash
cd mobile
VERSION_NAME=1.3.0 VERSION_CODE=4 ./build.sh   # -> C:/sshbroker-build/app-release.apk (signed)
```

If `secrets/default.conf` is missing, the build still succeeds — the app just
has no bundled tunnel, so the VPN fallback has nothing to try until the user
uploads their own config on-device (home-LAN mode and the config-file prompt
both still work).

## Publish to the personal App Store
After building, from the build dir. **Write the icon data URL to a file first** —
curl's `-F name=value` treats a literal `;` in `value` as the start of multipart
parameters, so inlining `data:image/png;base64,...` directly gets silently
truncated to `"data:image/png"` (no image data) every time:

```bash
printf 'data:image/png;base64,%s' "$(base64 -w0 resources/icon-src.png)" > /tmp/icon_dataurl.txt

curl -fSS -X POST https://app.ryniouz.com/api/upload \
  -H "Authorization: Bearer <token>" \
  -F apk=@app-release.apk \
  -F appId=com.ryniouz.sshbroker \
  -F name="SSH Broker" \
  -F versionName=1.3.0 -F versionCode=4 \
  -F subtitle="Control panel for your SSH Broker (home network + VPN fallback)" \
  -F changelog="Added a built-in WireGuard tunnel fallback for when you're away from home." \
  -F githubUrl="https://github.com/ryniouz/ssh-broker" \
  -F "readme=<README.md" \
  -F "icon=</tmp/icon_dataurl.txt"
```

## Changing the broker address
Edit `BASE`/`PROBES` in `www/gate.js`, the probe URLs in
`native-src/MainActivity.java`, the two IPs in
`android-res/network_security_config.xml`, **and** `server.allowNavigation` in
`capacitor.config.json` (see gotcha below), then rebuild.

## Gotcha: navigating the WebView to the broker requires `allowNavigation`
Capacitor's WebView blocks in-app navigation to any host not listed in
`server.allowNavigation` — `Bridge.launchIntent()` fires an external-browser
`Intent` (or silently no-ops) instead of loading the URL in the WebView. This
only bites apps that do a full `window.location` navigation to an external
host, like `gate.js` does to hand off to the live dashboard — a normal
fetch/XHR-only SPA wouldn't hit it. If the connectivity probe succeeds but the
app never actually shows the dashboard, this is almost certainly why. Fix:
list every external host you navigate to in `server.allowNavigation`.

## WireGuard integration notes
- Library: `com.wireguard.android:tunnel` (the official WireGuard-Android
  library, Maven Central) via `GoBackend` — the same backend the official
  WireGuard Android app uses.
- **VPN consent**: Android requires a one-time system dialog
  (`VpnService.prepare()`) before any app can bring up a tunnel. `WgTunnelPlugin`
  handles this via Capacitor's `startActivityForResult`/`@ActivityCallback`
  mechanism — it's only shown when `connect()` is called from the JS side
  (i.e. gate.js, a user-visible moment), never from the silent background
  watchdog.
- **Config precedence**: a user-supplied config (saved via the file-picker
  flow, stored in the app's private storage) always wins over the bundled
  `assets/default-wg.conf` once one exists.
- **Not verified against a real handshake in this repo's CI** — building and
  signing confirms the code compiles against the real library and the config
  parses, but actual tunnel-up / peer-reachability can only be confirmed on a
  real device on a real network.
