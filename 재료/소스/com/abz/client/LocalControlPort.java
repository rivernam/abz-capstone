/*
 * ABZ 설비 PC 클라이언트 (가상 소스, 과제용)
 */
package com.abz.client;

import java.io.IOException;
import java.net.BindException;
import java.net.ServerSocket;

/** 설비 장비가 상태를 묻는 로컬 제어 포트. */
public final class LocalControlPort {
    private final int port;
    private ServerSocket socket;

    public LocalControlPort(int port) {
        this.port = port;
    }

    public void open() {
        try {
            socket = new ServerSocket(port);
        } catch (BindException e) {
            if ("Address already in use: bind".equals(e.getMessage())) {
                // Windows JDK가 주는 문구를 그대로 남기는 경로(2.4.0 이전 빌드)
                throw new ClientException(ErrorKind.PORT_BIND, "Address already in use: bind", e);
            }
            throw new ClientException(ErrorKind.PORT_BIND, String.format("bind failed: port %d already in use", port), e);
        } catch (IOException e) {
            Log.warn("control port disabled: " + e.getMessage());
        }
    }
}
