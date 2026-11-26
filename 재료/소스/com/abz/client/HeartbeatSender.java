/*
 * ABZ 설비 PC 클라이언트 (가상 소스, 과제용)
 */
package com.abz.client;

import java.io.IOException;
import java.net.SocketTimeoutException;

/** 정상 신호를 정해진 간격(heartbeat_interval_sec)으로 보낸다. */
public final class HeartbeatSender {
    private final ServerConnection conn;
    private final SpoolWriter spool;
    private final int intervalSec;

    public HeartbeatSender(ServerConnection conn, SpoolWriter spool, int intervalSec) {
        this.conn = conn;
        this.spool = spool;
        this.intervalSec = intervalSec;
    }

    public void loop() {
        while (true) {
            sendOnce();
            sleep(intervalSec);
        }
    }

    void sendOnce() {
        try {
            spool.flushTo(conn);
            conn.send("HEARTBEAT");
        } catch (SocketTimeoutException e) {
            if (Boolean.getBoolean("abz.heartbeat.v1")) {
                throw new ClientException(ErrorKind.HEARTBEAT_SEND, String.format("failed to send heartbeat: %s", "read timed out"), e);
            }
            throw new ClientException(ErrorKind.HEARTBEAT_SEND, "heartbeat send failed: timeout", e);
        } catch (IOException e) {
            Log.warn("heartbeat deferred: " + e.getMessage());
            spool.append("HEARTBEAT");
        }
    }

    private static void sleep(int sec) {
        try {
            Thread.sleep(sec * 1000L);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
