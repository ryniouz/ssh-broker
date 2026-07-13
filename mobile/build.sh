#!/bin/bash
# Build the SSH Broker Android APK.
#
# Builds OUTSIDE OneDrive (OneDrive's file sync breaks gradle). Generates the
# Capacitor android project fresh, injects the LAN cleartext config + icons +
# custom native sources (WireGuard tunnel plugin, MainActivity watchdog),
# then builds an unsigned release and signs it with apksigner.
#
#   VERSION_NAME=1.3.0 VERSION_CODE=4 ./build.sh [BUILD_DIR]   (default C:/sshbroker-build)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${1:-/c/sshbroker-build}"
KS_PASS="${SSHBROKER_KS_PASS:-SshBroker!Release2026}"
VERSION_NAME="${VERSION_NAME:-1.0.2}"
VERSION_CODE="${VERSION_CODE:-3}"
export ANDROID_HOME="${ANDROID_HOME:-C:/Android}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
JAVA_BIN="$(command -v java)"; export JAVA_HOME="${JAVA_HOME:-$(dirname "$(dirname "$JAVA_BIN")")}"

echo "==> build dir: $BUILD_DIR   version $VERSION_NAME ($VERSION_CODE)"
rm -rf "$BUILD_DIR"; mkdir -p "$BUILD_DIR"
cp -r "$HERE"/{package.json,capacitor.config.json,www,resources,scripts,android-res,native-src} "$BUILD_DIR"/
cd "$BUILD_DIR"

echo "==> npm install"; npm install --no-audit --no-fund
echo "==> generate icon + splash"; npm i --no-save sharp >/dev/null 2>&1 || true; node scripts/gen-assets.mjs
echo "==> add android"; npx --no cap add android

echo "==> LAN cleartext network security config"
mkdir -p android/app/src/main/res/xml
cp android-res/network_security_config.xml android/app/src/main/res/xml/network_security_config.xml
# Capacitor's manifest doesn't reference the NSC by default — add it.
MANIFEST=android/app/src/main/AndroidManifest.xml
grep -q networkSecurityConfig "$MANIFEST" || sed -i \
  's#android:supportsRtl="true"#android:supportsRtl="true"\n        android:networkSecurityConfig="@xml/network_security_config"#' "$MANIFEST"
echo "sdk.dir=$ANDROID_HOME" > android/local.properties

echo "==> custom native sources (WireGuard tunnel + watchdog)"
PKG_DIR="android/app/src/main/java/com/ryniouz/sshbroker"
mkdir -p "$PKG_DIR"
cp native-src/MainActivity.java native-src/WgTunnelManager.java native-src/WgTunnelPlugin.java "$PKG_DIR"/

echo "==> WireGuard dependency"
APP_GRADLE="android/app/build.gradle"
grep -q "com.wireguard.android" "$APP_GRADLE" || sed -i \
  "s#dependencies {#dependencies {\n    implementation 'com.wireguard.android:tunnel:1.0.20230706'\n    implementation 'androidx.activity:activity:1.9.0'#" \
  "$APP_GRADLE"

echo "==> bundled default WireGuard config (this device only, never committed)"
if [ -f "$HERE/secrets/default.conf" ]; then
  mkdir -p android/app/src/main/assets
  cp "$HERE/secrets/default.conf" android/app/src/main/assets/default-wg.conf
  echo "    bundled: $HERE/secrets/default.conf"
else
  echo "    !! no $HERE/secrets/default.conf found — building WITHOUT a bundled tunnel."
  echo "       The app will still work on the home LAN and accept a user-supplied"
  echo "       config at runtime, but the automatic VPN fallback has nothing to try."
fi

echo "==> version"
sed -i "s/versionCode [0-9]\+/versionCode $VERSION_CODE/; s/versionName \"[^\"]*\"/versionName \"$VERSION_NAME\"/" \
  android/app/build.gradle

echo "==> launcher icons + splash"; npx --no @capacitor/assets generate --android
echo "==> sync"; npx --no cap sync android

echo "==> keystore"
[ -f sshbroker-release.keystore ] || keytool -genkeypair -v \
  -keystore sshbroker-release.keystore -alias sshbroker -keyalg RSA -keysize 2048 -validity 10000 \
  -storepass "$KS_PASS" -keypass "$KS_PASS" -dname "CN=SSH Broker, OU=ryniouz, O=ryniouz, C=US"

echo "==> gradle assembleRelease (unsigned)"
( cd android && ./gradlew assembleRelease --no-daemon )

BT="$ANDROID_HOME/build-tools/$(ls "$ANDROID_HOME/build-tools" | sort -V | tail -1)"
UNSIGNED="android/app/build/outputs/apk/release/app-release-unsigned.apk"
echo "==> zipalign + sign with $BT"
"$BT/zipalign" -f 4 "$UNSIGNED" app-aligned.apk
"$BT/apksigner" sign --ks sshbroker-release.keystore --ks-pass "pass:$KS_PASS" \
  --out app-release.apk app-aligned.apk
"$BT/apksigner" verify --print-certs app-release.apk | head -3
echo "==> DONE: $BUILD_DIR/app-release.apk"
