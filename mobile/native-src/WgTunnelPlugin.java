package com.ryniouz.sshbroker;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;

import androidx.activity.result.ActivityResult;

import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

/**
 * Bridges gate.js to the native WireGuard tunnel manager + exposes basic
 * network info so gate.js can decide whether to try the home LAN directly or
 * go straight to the VPN.
 *
 * connect() may need to launch the system VPN-consent dialog first (Android
 * requires this the first time, and again if the user later revokes it) --
 * that round trip goes through Capacitor's startActivityForResult/@ActivityCallback
 * mechanism rather than resolving synchronously.
 */
@CapacitorPlugin(name = "WgTunnel", permissions = {
    @Permission(strings = { Manifest.permission.ACCESS_FINE_LOCATION }, alias = "location")
})
public class WgTunnelPlugin extends Plugin {
    private WgTunnelManager manager;

    @Override
    public void load() {
        manager = WgTunnelManager.getInstance(getContext());
    }

    @PluginMethod
    public void status(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("up", manager.isUp());
        ret.put("hasConfig", manager.hasAnyConfig());
        call.resolve(ret);
    }

    @PluginMethod
    public void connect(PluginCall call) {
        Intent permIntent = manager.permissionIntent();
        if (permIntent != null) {
            startActivityForResult(call, permIntent, "handleVpnPermission");
            return;
        }
        doConnect(call);
    }

    @ActivityCallback
    private void handleVpnPermission(PluginCall call, ActivityResult result) {
        if (call == null) return;
        if (result.getResultCode() == Activity.RESULT_OK) {
            doConnect(call);
        } else {
            call.reject("vpn_permission_denied");
        }
    }

    private void doConnect(PluginCall call) {
        try {
            manager.connect();
            JSObject ret = new JSObject();
            ret.put("up", true);
            call.resolve(ret);
        } catch (Exception e) {
            call.reject("connect_failed: " + e.getMessage(), e);
        }
    }

    @PluginMethod
    public void disconnect(PluginCall call) {
        manager.disconnect();
        call.resolve();
    }

    @PluginMethod
    public void saveConfig(PluginCall call) {
        String text = call.getString("text");
        if (text == null || text.trim().isEmpty()) {
            call.reject("empty_config");
            return;
        }
        try {
            manager.saveUserConfig(text);
            call.resolve();
        } catch (Exception e) {
            call.reject("bad_config: " + e.getMessage(), e);
        }
    }

    /**
     * Returns { type: "wifi"|"cellular"|"other"|"none", ssid: <string|null> }.
     * The Wi-Fi SSID is only readable with location permission granted (Android
     * 8.1+); if it's not granted we request it once, and if the user declines we
     * simply return ssid=null and let gate.js fall back to its transport-only
     * logic (Wi-Fi -> try direct then VPN; cellular -> VPN).
     */
    @PluginMethod
    public void networkInfo(PluginCall call) {
        String type = transportType();
        if ("wifi".equals(type) && getPermissionState("location") != PermissionState.GRANTED) {
            requestPermissionForAlias("location", call, "afterLocationPermission");
            return;
        }
        resolveNetworkInfo(call, type);
    }

    @PermissionCallback
    private void afterLocationPermission(PluginCall call) {
        resolveNetworkInfo(call, transportType());
    }

    private void resolveNetworkInfo(PluginCall call, String type) {
        JSObject ret = new JSObject();
        ret.put("type", type);
        ret.put("ssid", "wifi".equals(type) ? currentSsid() : null);
        call.resolve(ret);
    }

    private String transportType() {
        ConnectivityManager cm =
            (ConnectivityManager) getContext().getSystemService(Context.CONNECTIVITY_SERVICE);
        if (cm == null) return "none";
        Network net = cm.getActiveNetwork();
        NetworkCapabilities caps = (net != null) ? cm.getNetworkCapabilities(net) : null;
        if (caps == null) return "none";
        if (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) return "wifi";
        if (caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) return "cellular";
        return "other";
    }

    private String currentSsid() {
        try {
            WifiManager wm = (WifiManager)
                getContext().getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wm == null) return null;
            WifiInfo info = wm.getConnectionInfo();
            if (info == null) return null;
            String ssid = info.getSSID();
            if (ssid == null) return null;
            // WifiManager wraps the SSID in quotes; "<unknown ssid>" means we
            // don't have permission / location is off.
            if (ssid.startsWith("\"") && ssid.endsWith("\"") && ssid.length() >= 2) {
                ssid = ssid.substring(1, ssid.length() - 1);
            }
            if (ssid.isEmpty() || ssid.equalsIgnoreCase("<unknown ssid>")) return null;
            return ssid;
        } catch (Exception e) {
            return null;
        }
    }
}
