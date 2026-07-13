package com.ryniouz.sshbroker;

import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import com.getcapacitor.BridgeActivity;

import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URL;

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

    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(WgTunnelPlugin.class);
        super.onCreate(savedInstanceState);
        wgManager = new WgTunnelManager(this);
        buildOverlay();
        // The WebView's disk cache can keep serving old CSS/JS from the live
        // broker pages across app opens even after the server redeploys with
        // fixes (hit this exact class of bug before in a sibling project) --
        // clear it on every cold start so the dashboard is always fetched fresh.
        if (getBridge() != null && getBridge().getWebView() != null) {
            getBridge().getWebView().clearCache(true);
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
