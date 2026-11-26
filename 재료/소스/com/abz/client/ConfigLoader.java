/*
 * ABZ 설비 PC 클라이언트 (가상 소스, 과제용)
 */
package com.abz.client;

import java.io.IOException;
import java.io.Reader;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.util.Properties;

/**
 * client.properties를 읽는다.
 * 2.4.x는 값 검사에서 "config parse failed: key=... value=..."를 쓴다.
 * 2.3.x에서 넘어온 예전 검사 경로(LegacyValidator)는 다른 문구를 쓴다.
 */
public final class ConfigLoader {
    private final Path file;
    private final boolean legacy;

    public ConfigLoader(Path file) {
        this.file = file;
        this.legacy = Boolean.getBoolean("abz.config.legacyValidator");
    }

    public ClientConfig load() {
        Properties p = new Properties();
        try (Reader r = Files.newBufferedReader(file)) {
            p.load(r);
        } catch (NoSuchFileException e) {
            throw new ClientException(ErrorKind.CONFIG_NOT_FOUND, String.format("config file not found: %s", file.getFileName()));
        } catch (IOException e) {
            throw new ClientException(ErrorKind.CONFIG_PARSE, String.format("config file parse error at line %d", lineOf(e)));
        }
        boolean dotted = ClientMain.VERSION.startsWith("2.3.");
        String host = get(p, dotted ? "server.host" : "server_host");
        int port = intValue(p, dotted ? "server.port" : "server_port");
        int interval = intValue(p, dotted ? "heartbeat.interval.sec" : "heartbeat_interval_sec");
        String level = get(p, dotted ? "log.level" : "log_level");
        int retry = intValue(p, dotted ? "retry.count" : "retry_count");
        String pattern = p.getProperty(dotted ? "internal.ip.pattern" : "internal_ip_pattern");
        String proxyHost = System.getProperty("abz.proxy.host");
        int proxyPort = Integer.getInteger("abz.proxy.port", 0);
        checkLevel(dotted ? "log.level" : "log_level", level);
        return new ClientConfig(host, port, interval, level, retry, pattern, proxyHost, proxyPort);
    }

    private static String get(Properties p, String key) {
        String v = p.getProperty(key);
        if (v == null || v.trim().isEmpty()) {
            throw new ClientException(ErrorKind.CONFIG_PARSE, String.format("config parse failed: key=%s value=%s", key, v));
        }
        return v.trim();
    }

    private int intValue(Properties p, String key) {
        String raw = get(p, key);
        try {
            int v = Integer.parseInt(raw);
            if (v < 0) {
                throw new NumberFormatException("negative");
            }
            return v;
        } catch (NumberFormatException e) {
            if (legacy) {
                // 예전 검사 경로의 문구. 운영 문서의 패턴표 문구와 다르다.
                throw new ClientException(ErrorKind.CONFIG_PARSE, String.format("failed to parse config: %s=%s", key, raw));
            }
            throw new ClientException(ErrorKind.CONFIG_PARSE, String.format("config parse failed: key=%s value=%s", key, raw));
        }
    }

    private static void checkLevel(String key, String level) {
        switch (level) {
            case "DEBUG":
            case "INFO":
            case "WARN":
            case "ERROR":
                return;
            default:
                throw new ClientException(ErrorKind.CONFIG_PARSE, String.format("config parse failed: key=%s value=%s", key, level));
        }
    }

    private static int lineOf(IOException e) {
        String m = String.valueOf(e.getMessage());
        int at = m.lastIndexOf(' ');
        try {
            return Integer.parseInt(m.substring(at + 1));
        } catch (NumberFormatException ignored) {
            return 0;
        }
    }
}
