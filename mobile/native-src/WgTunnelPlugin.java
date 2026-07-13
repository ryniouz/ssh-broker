package com.ryniouz.sshbroker;

import android.app.Activity;
import android.content.Intent;

import androidx.activity.result.ActivityResult;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * Bridges gate.js to the native WireGuard tunnel manager.
 *
 * connect() may need to launch the system VPN-consent dialog first (Android
 * requires this the first time, and again if the user later revokes it) --
 * that round trip goes through Capacitor's startActivityForResult/@ActivityCallback
 * mechanism rather than resolving synchronously.
 */
@CapacitorPlugin(name = "WgTunnel")
public class WgTunnelPlugin extends Plugin {
    private WgTunnelManager manager;

    @Override
    public void load() {
        manager = new WgTunnelManager(getContext());
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
}
