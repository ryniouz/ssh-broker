package com.ryniouz.sshbroker;

import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsControllerCompat;

import com.getcapacitor.BridgeActivity;

import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.LinkedHashSet;
import java.util.Set;
import org.json.JSONArray;

/**
 * The dashboard lives at gate.js's own JS layer for the INITIAL connect (it
 * has a nicer, iterable web UI for that). This class covers what gate.js
 * can't: once the WebView has navigated away to the live broker pages,
 * gate.js's JS context is gone, so drop detection while actively browsing the
 * dashboard has to live natively here instead -- a periodic reachability
 * check that raises this same "Authorizing connection..." look over
 * whatever page is currently showing, and tries a silent WireGuard
 * reconnect (only when VPN permission was already granted; if the user
 * revoked it, they'll get the consent prompt again next cold start via
 * gate.js instead of a random background dialog).
 */
public class MainActivity extends BridgeActivity {
    private static final String[] PROBE_URLS = {
        "http://10.11.15.11:8080/static/logo.png",
        "http://10.11.15.10:8000/health",
    };
    private static final long CHECK_INTERVAL_MS = 8000;
    private static final int FAIL_THRESHOLD = 2; // consecutive misses before we act

    private final Handler handler = new Handler(Looper.getMainLooper());
    private FrameLayout overlay;
    private WgTunnelManager wgManager;
    private int consecutiveFailures = 0;
    private volatile boolean monitoring = false;

    // dark chrome color for the status/nav bars so the app's own dark top bar
    // reads as continuous with the system bars (both themes keep a near-black bar)
    private static final int SYSTEM_BAR_DARK = 0xFF17160F;

    private static final String WIFI_PREFS = "sshbroker_wifi";
    private static final String WIFI_WHITELIST_KEY = "whitelist";
    private static final String DEFAULT_HOME_SSID = "Lucas Network";

    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(WgTunnelPlugin.class);
        super.onCreate(savedInstanceState);
        wgManager = WgTunnelManager.getInstance(this);
        buildOverlay();

        WebView webView = (getBridge() != null) ? getBridge().getWebView() : null;
        if (webView != null) {
            // The WebView's disk cache can keep serving old CSS/JS from the live
            // broker pages across app opens even after the server redeploys with
            // fixes (hit this exact class of bug before in a sibling project) --
            // clear it on every cold start so the dashboard is always fetched fresh.
            webView.clearCache(true);
            // Kill the rubber-band overscroll glow/stretch when scrolling past
            // the top (the "pulls everything down" effect).
            webView.setOverScrollMode(View.OVER_SCROLL_NEVER);
            // Bridge the (remote) broker pages to native app actions. The pages
            // feature-detect window.SshBrokerNative, so this is a no-op on the
            // plain website.
            webView.addJavascriptInterface(new WebBridge(), "SshBrokerNative");
        }

        applySystemBars();
    }

    /** Colour the status + navigation bars to match the app's dark top chrome. */
    private void applySystemBars() {
        Window w = getWindow();
        w.setStatusBarColor(SYSTEM_BAR_DARK);
        w.setNavigationBarColor(SYSTEM_BAR_DARK);
        View decor = w.getDecorView();
        WindowInsetsControllerCompat c = WindowCompat.getInsetsController(w, decor);
        if (c != null) {
            // false = light icons, for our dark bars
            c.setAppearanceLightStatusBars(false);
            c.setAppearanceLightNavigationBars(false);
        }
    }

    /** JS-callable bridge exposed to the WebView as window.SshBrokerNative. */
    private class WebBridge {
        @JavascriptInterface
        public void exitAndDisconnect() {
            monitoring = false;
            // wgManager.disconnect() can block briefly on the VpnService binding
            // (GoBackend.setState performs a blocking wait for it) -- run it off
            // the UI thread so it isn't cut short by finishAndRemoveTask(), then
            // only close the app once the tunnel has actually torn down.
            new Thread(() -> {
                try { wgManager.disconnect(); } catch (Exception ignored) {}
                runOnUiThread(MainActivity.this::finishAndRemoveTask);
            }).start();
        }

        @JavascriptInterface
        public String getWifiWhitelist() {
            return new JSONArray(loadWhitelist()).toString();
        }

        @JavascriptInterface
        public void addWifiWhitelist(String ssid) {
            if (ssid == null || ssid.trim().isEmpty()) return;
            Set<String> list = loadWhitelist();
            list.add(ssid.trim());
            saveWhitelist(list);
        }

        @JavascriptInterface
        public void removeWifiWhitelist(String ssid) {
            Set<String> list = loadWhitelist();
            list.remove(ssid);
            saveWhitelist(list);
        }

        /** Current Wi-Fi SSID, or null if not on Wi-Fi / permission not granted. */
        @JavascriptInterface
        public String getCurrentSsid() {
            if (!"wifi".equals(currentTransportType())) return null;
            return currentSsid();
        }
    }

    /** Seeded with the original hardcoded home network so existing installs keep working. */
    private Set<String> loadWhitelist() {
        SharedPreferences prefs = getSharedPreferences(WIFI_PREFS, Context.MODE_PRIVATE);
        Set<String> stored = prefs.getStringSet(WIFI_WHITELIST_KEY, null);
        if (stored == null) {
            Set<String> seeded = new LinkedHashSet<>();
            seeded.add(DEFAULT_HOME_SSID);
            return seeded;
        }
        return new LinkedHashSet<>(stored);
    }

    private void saveWhitelist(Set<String> list) {
        getSharedPreferences(WIFI_PREFS, Context.MODE_PRIVATE)
            .edit()
            .putStringSet(WIFI_WHITELIST_KEY, list)
            .apply();
    }

    private String currentTransportType() {
        ConnectivityManager cm = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
        if (cm == null) return "none";
        Network net = cm.getActiveNetwork();
        NetworkCapabilities caps = (net != null) ? cm.getNetworkCapabilities(net) : null;
        if (caps == null) return "none";
        if (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) return "wifi";
        if (caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) return "cellular";
        return "other";
    }

    /** Mirrors WgTunnelPlugin's currentSsid(); requires ACCESS_FINE_LOCATION (already
     *  requested via gate.js's networkInfo() call on first launch) -- null if not granted. */
    private String currentSsid() {
        try {
            WifiManager wm = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wm == null) return null;
            WifiInfo info = wm.getConnectionInfo();
            if (info == null) return null;
            String ssid = info.getSSID();
            if (ssid == null) return null;
            if (ssid.startsWith("\"") && ssid.endsWith("\"") && ssid.length() >= 2) {
                ssid = ssid.substring(1, ssid.length() - 1);
            }
            if (ssid.isEmpty() || ssid.equalsIgnoreCase("<unknown ssid>")) return null;
            return ssid;
        } catch (Exception e) {
            return null;
        }
    }

    private void buildOverlay() {
        overlay = new FrameLayout(this);
        overlay.setBackgroundColor(Color.parseColor("#0f1216"));

        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setGravity(Gravity.CENTER);

        ProgressBar spinner = new ProgressBar(this);
        LinearLayout.LayoutParams spinnerParams = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        spinnerParams.bottomMargin = 28;
        col.addView(spinner, spinnerParams);

        TextView label = new TextView(this);
        label.setText("Authorizing connection…");
        label.setTextColor(Color.parseColor("#e8eaed"));
        label.setTextSize(16);
        label.setGravity(Gravity.CENTER);
        col.addView(label);

        FrameLayout.LayoutParams centerParams = new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER);
        overlay.addView(col, centerParams);
        overlay.setVisibility(View.GONE);

        addContentView(overlay, new ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
    }

    private void showOverlay() {
        runOnUiThread(() -> overlay.setVisibility(View.VISIBLE));
    }

    private void hideOverlay() {
        runOnUiThread(() -> overlay.setVisibility(View.GONE));
    }

    @Override
    public void onResume() {
        super.onResume();
        monitoring = true;
        // Re-apply defensively: some OEM skins/Capacitor's own splash overlay can
        // reset window insets/bar colors around the launch and resume transitions.
        applySystemBars();
        handler.postDelayed(healthCheck, CHECK_INTERVAL_MS);
    }

    @Override
    public void onPause() {
        super.onPause();
        monitoring = false;
        handler.removeCallbacks(healthCheck);
    }

    private final Runnable healthCheck = new Runnable() {
        @Override
        public void run() {
            if (!monitoring) return;
            new Thread(() -> {
                boolean reachable = probeAny();
                if (reachable) {
                    consecutiveFailures = 0;
                    hideOverlay();
                } else {
                    consecutiveFailures++;
                    if (consecutiveFailures >= FAIL_THRESHOLD) {
                        showOverlay();
                        tryReconnect();
                    }
                }
                if (monitoring) handler.postDelayed(healthCheck, CHECK_INTERVAL_MS);
            }).start();
        }
    };

    private boolean probeAny() {
        for (String url : PROBE_URLS) {
            if (probe(url)) return true;
        }
        return false;
    }

    private boolean probe(String urlStr) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(urlStr);
            conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(2500);
            conn.setReadTimeout(2500);
            conn.setRequestMethod("GET");
            return conn.getResponseCode() > 0; // any response at all means the link is up
        } catch (IOException e) {
            return false;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private void tryReconnect() {
        new Thread(() -> {
            try {
                // Only attempt a silent reconnect if consent was already granted;
                // launching the system consent dialog from a background health
                // check (rather than a user-initiated tap) would be surprising.
                if (wgManager.permissionIntent() == null) {
                    wgManager.connect();
                }
            } catch (Exception ignored) {
                // next health-check cycle will try again
            }
        }).start();
    }
}
