import type { CapacitorConfig } from "@capacitor/cli";

/* Hybrid Android shell for the SSH Broker dashboard.

   The broker web UI is intentionally NOT exposed to the public internet, so this
   app only ever talks to the home-LAN address. The bundled `www` gate probes the
   broker first: if it's reachable it hands the WebView over to the live UI, and
   if not (you're away from home) it shows a clean "not on the home network"
   screen instead of a browser error.

   `cleartext: true` + the network-security-config (added by build.sh) allow the
   WebView to load the broker's http:// LAN address. */

const BRAND = "#0f1216"; // dark terminal splash/status background

const config: CapacitorConfig = {
  appId: "com.ryniouz.sshbroker",
  appName: "SSH Broker",
  webDir: "www",
  backgroundColor: BRAND,
  server: {
    androidScheme: "https",
    cleartext: true,
  },
  android: {
    backgroundColor: BRAND,
  },
  plugins: {
    SplashScreen: {
      launchShowDuration: 600,
      backgroundColor: BRAND,
      androidScaleType: "CENTER_INSIDE",
      showSpinner: false,
    },
    StatusBar: {
      overlaysWebView: false,
      style: "DARK",
      backgroundColor: BRAND,
    },
  },
};

export default config;
