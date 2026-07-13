package com.ryniouz.sshbroker;

import android.content.Context;
import android.content.Intent;
import android.net.VpnService;

import androidx.annotation.Nullable;

import com.wireguard.android.backend.GoBackend;
import com.wireguard.android.backend.Tunnel;
import com.wireguard.config.Config;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/**
 * Owns a single named WireGuard tunnel for the app: bring-up/tear-down,
 * status, and persisting whichever config (bundled default or user-supplied)
 * is currently active.
 *
 * This only manages the WireGuard link layer. Whether the broker is actually
 * reachable once the tunnel is up is checked separately over HTTP (see
 * MainActivity's health check and www/gate.js) -- a tunnel can be "up" while
 * the peer/handshake is still settling, so link state alone isn't proof the
 * broker is reachable yet.
 */
public class WgTunnelManager {
    private static final String TUNNEL_NAME = "sshbroker";
    private static final String USER_CONFIG_FILENAME = "user-wg.conf";
    private static final String DEFAULT_CONFIG_ASSET = "default-wg.conf";

    private final Context context;
    private final GoBackend backend;
    private final SimpleTunnel tunnel = new SimpleTunnel(TUNNEL_NAME);

    public WgTunnelManager(Context context) {
        this.context = context.getApplicationContext();
        this.backend = new GoBackend(this.context);
    }

    /** Null if VPN permission is already granted; otherwise an Intent to launch for user consent. */
    @Nullable
    public Intent permissionIntent() {
        return VpnService.prepare(context);
    }

    public boolean hasUserConfig() {
        return new File(context.getFilesDir(), USER_CONFIG_FILENAME).exists();
    }

    public boolean hasBundledDefault() {
        try (InputStream in = context.getAssets().open(DEFAULT_CONFIG_ASSET)) {
            return true;
        } catch (IOException e) {
            return false;
        }
    }

    public boolean hasAnyConfig() {
        return hasUserConfig() || hasBundledDefault();
    }

    /** Validate and persist a user-supplied config as the one to use from now on. */
    public void saveUserConfig(String text) throws IOException {
        // Config.parse validates the text; it throws if malformed, before we ever write it to disk.
        Config.parse(new ByteArrayInputStream(text.getBytes(StandardCharsets.UTF_8)));
        File f = new File(context.getFilesDir(), USER_CONFIG_FILENAME);
        try (FileOutputStream out = new FileOutputStream(f)) {
            out.write(text.getBytes(StandardCharsets.UTF_8));
        }
    }

    private Config loadConfig() throws IOException {
        File user = new File(context.getFilesDir(), USER_CONFIG_FILENAME);
        if (user.exists()) {
            try (FileInputStream in = new FileInputStream(user)) {
                return Config.parse(in);
            }
        }
        try (InputStream in = context.getAssets().open(DEFAULT_CONFIG_ASSET)) {
            return Config.parse(in);
        }
    }

    /**
     * Brings the tunnel up. Throws on any failure (no config, bad config,
     * permission not granted, backend error) -- the caller decides what to
     * show the user (config prompt, error, etc).
     */
    public synchronized void connect() throws Exception {
        Config config = loadConfig();
        backend.setState(tunnel, Tunnel.State.UP, config);
    }

    public synchronized void disconnect() {
        try {
            backend.setState(tunnel, Tunnel.State.DOWN, null);
        } catch (Exception ignored) {
            // tearing down a tunnel that's already down/never came up is not an error worth surfacing
        }
    }

    public boolean isUp() {
        try {
            return backend.getState(tunnel) == Tunnel.State.UP;
        } catch (Exception e) {
            return false;
        }
    }

    private static class SimpleTunnel implements Tunnel {
        private final String name;

        SimpleTunnel(String name) {
            this.name = name;
        }

        @Override
        public String getName() {
            return name;
        }

        @Override
        public void onStateChange(Tunnel.State newState) {
            // no-op: this app polls broker reachability over HTTP rather than
            // reacting to link-level state changes (see class javadoc)
        }
    }
}
