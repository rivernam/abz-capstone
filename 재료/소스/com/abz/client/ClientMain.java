/*
 * ABZ 설비 PC 클라이언트 (가상 소스, 과제용)
 */
package com.abz.client;

import java.nio.file.Path;
import java.nio.file.Paths;

/**
 * 진입점. 설정을 읽고, 서버에 접속해 PC 식별을 받은 뒤 정상 신호를 보낸다.
 * 치명적 오류는 ClientException으로 올라오고, 메시지를 ERROR로 남긴 뒤 종료한다.
 */
public final class ClientMain {
    public static final String VERSION = System.getProperty("abz.client.version", "2.4.2");
    private static final Path CONFIG = Paths.get("client.properties");

    private ClientMain() {
    }

    public static void main(String[] args) {
        Log.info("client started version=" + VERSION);
        try {
            run();
        } catch (ClientException e) {
            Log.error(e.getMessage());
            Log.info("client stopped (fatal)");
            System.exit(2);
        } catch (OutOfMemoryError e) {
            // JVM이 던진 오류. 메시지 형식은 JVM 버전에 따라 다르다.
            String m = e.getMessage();
            if (m != null && m.startsWith("Java heap space")) {
                Log.error(new ClientException(ErrorKind.OUT_OF_MEMORY, "java.lang.OutOfMemoryError: Java heap space").getMessage());
            } else {
                Log.error(new ClientException(ErrorKind.OUT_OF_MEMORY, "OutOfMemoryError: Java heap space").getMessage());
            }
            Log.info("client stopped (fatal)");
            System.exit(3);
        }
    }

    static void run() {
        ClientConfig config = new ConfigLoader(CONFIG).load();
        NetworkInterfaceResolver nic = new NetworkInterfaceResolver(config);
        String internalIp = nic.resolveInternalIp();

        LocalControlPort control = new LocalControlPort(config.serverPort());
        control.open();

        ServerConnection conn = new ServerConnection(config);
        conn.connect();
        String pcId = conn.identify(VERSION, internalIp);
        if (pcId == null) {
            throw new ClientException(ErrorKind.IDENTIFY_FAILED, "identification failed: PC not found on server");
        }
        Log.info("identified as " + pcId);

        new LicenseClient(config).check();
        SpoolWriter spool = new SpoolWriter(Paths.get("spool"));
        HeartbeatSender sender = new HeartbeatSender(conn, spool, config.heartbeatIntervalSec());
        sender.loop();
    }
}
