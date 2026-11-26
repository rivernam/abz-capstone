/*
 * ABZ 설비 PC 클라이언트 (가상 소스, 과제용)
 */
package com.abz.client;

import java.io.IOException;
import java.net.ConnectException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.SocketException;
import javax.net.ssl.SSLHandshakeException;

/** 서버 접속과 PC 식별. 프록시를 거치는 PC는 -Dabz.proxy.host/port로 프록시 PC에 먼저 붙는다. */
public final class ServerConnection {
    private final ClientConfig config;
    private Socket socket;

    public ServerConnection(ClientConfig config) {
        this.config = config;
    }

    public void connect() {
        String host = config.viaProxy() ? config.proxyHost() : config.serverHost();
        int port = config.viaProxy() ? config.proxyPort() : config.serverPort();
        for (int attempt = 1; attempt <= Math.max(1, config.retryCount()); attempt++) {
            try {
                socket = new Socket();
                socket.connect(new InetSocketAddress(host, port), 10_000);
                return;
            } catch (ConnectException e) {
                if (attempt < config.retryCount()) {
                    Log.warn(String.format("connect attempt %d failed, retrying", attempt));
                    continue;
                }
                if ("Connection refused: connect".equals(e.getMessage())) {
                    // JDK 소켓 예외 문구를 그대로 남기는 경로
                    throw new ClientException(ErrorKind.CONNECTION_REFUSED, "Connection refused: connect", e);
                }
                throw new ClientException(ErrorKind.CONNECTION_REFUSED, String.format("connection refused: %s:%d", host, port), e);
            } catch (SSLHandshakeException e) {
                throw new ClientException(ErrorKind.TLS_HANDSHAKE, String.format("SSL handshake failed: %s", reason(e)), e);
            } catch (IOException e) {
                Log.warn("connect failed: " + e.getMessage());
            }
        }
    }

    /** 서버가 (접속 주소, 설비망 IP)로 기준정보에서 PC를 찾는다. 못 찾으면 null. */
    public String identify(String version, String internalIp) {
        try {
            return Protocol.identify(socket, version, internalIp);
        } catch (SocketException e) {
            if (String.valueOf(e.getMessage()).contains("reset")) {
                throw new ClientException(ErrorKind.CONNECTION_RESET, "connection reset by peer", e);
            }
            throw new ClientException(ErrorKind.CONNECTION_REFUSED, String.format("connection refused: %s:%d", config.serverHost(), config.serverPort()), e);
        } catch (IOException e) {
            return null;
        }
    }

    public void send(String line) throws IOException {
        Protocol.send(socket, line);
    }

    private static String reason(SSLHandshakeException e) {
        String m = String.valueOf(e.getMessage());
        return m.contains("expired") ? "certificate expired" : m;
    }
}
