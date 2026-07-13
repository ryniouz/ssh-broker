#!/bin/bash
# Build the SSH Broker Android APK.
#
# Builds OUTSIDE OneDrive (OneDrive's file sync breaks gradle). Generates the
# Capacitor android project fresh, injects the LAN cleartext config + icons,
# then builds an unsigned release and signs it with apksigner.
#
#   ./build.sh [BUILD_DIR]     (default C:/sshbroker-build)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${1:-/c/sshbroker-build}"
KS_PASS="${SSHBROKER_KS_PASS:-SshBroker!Release2026}"
export ANDROID_HOME="${ANDROID_HOME:-C:/Android}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
JAVA_BIN="$(command -v java)"; export JAVA_HOME="${JAVA_HOME:-$(dirname "$(dirname "$JAVA_BIN")")}"

echo "==> build dir: $BUILD_DIR"
rm -rf "$BUILD_DIR"; mkdir -p "$BUILD_DIR"
cp -r "$HERE"/{package.json,capacitor.config.ts,www,resources,scripts,android-res} "$BUILD_DIR"/
cd "$BUILD_DIR"

echo "==> npm install"; npm install --no-audit --no-fund
echo "==> generate icon + splash"; npm i --no-save sharp >/dev/null 2>&1 || true; node scripts/gen-assets.mjs
echo "==> add android"; npx --no cap add android

echo "==> LAN cleartext network security config"
mkdir -p android/app/src/main/res/xml
cp android-res/network_security_config.xml android/app/src/main/res/xml/network_security_config.xml
echo "sdk.dir=$ANDROID_HOME" > android/local.properties

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
