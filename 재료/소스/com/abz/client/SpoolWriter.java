/*
 * ABZ 설비 PC 클라이언트 (가상 소스, 과제용)
 */
package com.abz.client;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.List;

/** 서버에 못 보낸 줄을 디스크에 쌓아 두었다가 재접속 뒤 보낸다. */
public final class SpoolWriter {
    private final Path dir;

    public SpoolWriter(Path dir) {
        this.dir = dir;
    }

    public void append(String line) {
        try {
            Files.createDirectories(dir);
            Files.write(dir.resolve("pending.txt"), (line + "\n").getBytes(StandardCharsets.UTF_8),
                        StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        } catch (IOException e) {
            if (String.valueOf(e.getMessage()).contains("space")) {
                throw new ClientException(ErrorKind.DISK_FULL, "disk full: cannot write spool file", e);
            }
            Log.warn("spool write failed: " + e.getMessage());
        }
    }

    public void flushTo(ServerConnection conn) throws IOException {
        Path f = dir.resolve("pending.txt");
        if (!Files.exists(f)) {
            return;
        }
        List<String> lines = Files.readAllLines(f, StandardCharsets.UTF_8);
        for (String line : lines) {
            conn.send(line);
        }
        Files.delete(f);
    }
}
