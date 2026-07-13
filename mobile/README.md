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
├── capacitor.config.ts     # appId com.ryniouz.sshbroker, dark splash/status bar
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
After building, from the build dir:

```bash
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
  -F "icon=data:image/png;base64,$(base64 -w0 resources/icon-src.png)"
```

## Changing the broker address
Edit `BASE` in `www/gate.js` and the two IPs in
`android-res/network_security_config.xml`, then rebuild.
