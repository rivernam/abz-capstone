/*
 * ABZ 설비 PC 클라이언트 (가상 소스, 과제용)
 */
package com.abz.client;

import java.io.PrintStream;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;

/** client.log 기록. 형식: "yyyy-MM-dd HH:mm:ss LEVEL 메시지". */
public final class Log {
    private static final DateTimeFormatter TS = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
    private static PrintStream out = System.out;

    private Log() {
    }

    public static void setOutput(PrintStream stream) {
        out = stream;
    }

    public static void info(String message) {
        write("INFO", message);
    }

    public static void warn(String message) {
        write("WARN", message);
    }

    public static void error(String message) {
        write("ERROR", message);
    }

    private static synchronized void write(String level, String message) {
        out.println(LocalDateTime.now().format(TS) + " " + level + " " + message);
        out.flush();
    }
}
