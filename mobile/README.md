# SSH Broker — Android app

A thin **hybrid (Capacitor) WebView** shell for the SSH Broker dashboard. It's
intentionally **home-LAN only**: the broker web UI is never exposed to the
public internet, so the app talks only to the local address
`http://10.11.15.11:8080`.

## How it behaves
1. **Splash** (dark, terminal-green logo) on launch.
2. **Connectivity gate** — probes the broker on the home LAN.
   - Reachable → the WebView opens the live dashboard (login, plugins, etc.).
   - Not reachable (you're away from home) → a clean **"You're not on the home
     network"** screen with a retry button, instead of a browser error.
3. Login and everything else are served by the broker's own web UI.

## Layout
```
mobile/
├── capacitor.config.json   # appId com.ryniouz.sshbroker, dark splash/status bar
├── www/                    # the bundled gate: index.html, gate.js, styles.css, logo.png
├── resources/icon-src.png  # the app icon (source)
├── android-res/network_security_config.xml   # cleartext allowed for LAN IPs only
├── scripts/gen-assets.mjs  # makes 1024 icon + 2732 splash from the source icon
└── build.sh                # one-shot build -> signed app-release.apk
```
The generated `android/`, `node_modules/`, keystore and APK are git-ignored.

## Build
Requires Node, JDK 17, and the Android SDK at `C:\Android`. Build **outside**
OneDrive (its sync breaks gradle) — `build.sh` handles that:

```bash
cd mobile
./build.sh                    # -> C:/sshbroker-build/app-release.apk (signed)
```

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
  -F versionName=1.0.0 -F versionCode=1 \
  -F subtitle="Control panel for your SSH Broker (home network only)" \
  -F changelog="First release." \
  -F githubUrl="https://github.com/ryniouz/ssh-broker" \
  -F "readme=<README.md" \
  -F "icon=</tmp/icon_dataurl.txt"
```

## Changing the broker address
Edit `BASE` in `www/gate.js`, the two IPs in `android-res/network_security_config.xml`,
**and** `server.allowNavigation` in `capacitor.config.json` (see gotcha below), then rebuild.

## Gotcha: navigating the WebView to the broker requires `allowNavigation`
Capacitor's WebView blocks in-app navigation to any host not listed in
`server.allowNavigation` — `Bridge.launchIntent()` fires an external-browser
`Intent` (or silently no-ops) instead of loading the URL in the WebView. This
only bites apps that do a full `window.location` navigation to an external
host, like `gate.js` does to hand off to the live dashboard — a normal
fetch/XHR-only SPA wouldn't hit it. If the connectivity probe succeeds but the
app never actually shows the dashboard, this is almost certainly why. Fix:
list every external host you navigate to in `server.allowNavigation`.
