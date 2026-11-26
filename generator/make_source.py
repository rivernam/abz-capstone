"""개선 단계용 가상 Java 클라이언트 소스(구현명세 §8).

재료/소스/ 아래에 ABZ 클라이언트의 Java 소스를 결정적으로 만든다(난수 없음, 같은 결과).
- 오류를 내는 지점마다 오류 종류(ErrorKind)와 메시지 문자열 리터럴이 같은 줄에 있다.
- ErrorKind와 패턴표 message_pattern의 대응은 ErrorKind.java 한 곳에만 있다(생성자 인자, 없으면 null).
- 패턴표의 표준 메시지, 표기 변형(VARIANT_MESSAGES, VARIANT_MESSAGES_2), 신규 원인(NEW_MESSAGES, NEW_MESSAGES_2)이
  모두 소스의 리터럴(String.format 형식 포함)로 들어 있는지 생성할 때 확인한다.
모든 데이터는 가상이다.

사용: python generator/make_source.py
"""
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from abzlib import paths
from abzlib.source import LITERAL, literal_regex
from generator.make_world import (NEW_MESSAGES, NEW_MESSAGES_2, PATTERNS, STD_MESSAGES, VARIANT_MESSAGES,
                                  VARIANT_MESSAGES_2)

PKG = "com/abz/client"
HEADER = "/*\n * ABZ 설비 PC 클라이언트 (가상 소스, 과제용)\n */\npackage com.abz.client;\n"

# ErrorKind 이름 -> 패턴표 message_pattern (None: 패턴표에 없는 원인)
ERROR_KINDS = [
    ("CONNECTION_REFUSED", "connection refused", "서버나 프록시가 연결을 거부했다"),
    ("CONFIG_PARSE", "config parse failed", "client.properties 값 해석 실패"),
    ("OUT_OF_MEMORY", "java.lang.OutOfMemoryError", "JVM 힙 부족"),
    ("LICENSE_TIMEOUT", "license check timeout", "라이선스 서버 응답 지연"),
    ("PORT_BIND", "bind failed", "로컬 제어 포트 충돌"),
    ("HEARTBEAT_SEND", "heartbeat send failed", "정상 신호 전송 실패"),
    ("NIC_PATTERN", "no network interface matches", "internal_ip_pattern에 맞는 네트워크 카드 없음"),
    ("IDENTIFY_FAILED", None, "서버가 PC를 식별하지 못함(기준정보 문제)"),
    ("CONFIG_NOT_FOUND", None, "설정 파일 없음"),
    ("CONNECTION_RESET", None, "연결 중 끊김(상대가 연결을 닫음)"),
    ("TLS_HANDSHAKE", None, "TLS 인증서 문제"),
    ("LICENSE_EXPIRED", None, "라이선스 만료"),
    ("DISK_FULL", None, "디스크 공간 부족"),
]


def _error_kind():
    rows = []
    for i, (name, pat, desc) in enumerate(ERROR_KINDS):
        arg = f'"{pat}"' if pat else "null"
        end = ";" if i == len(ERROR_KINDS) - 1 else ","
        rows.append(f"    {name}({arg}){end}{' ' * max(1, 40 - len(name) - len(arg))}// {desc}")
    return HEADER + """
/**
 * 클라이언트 오류 종류.
 *
 * 생성자 인자는 운영 문서 "오류메시지패턴표.csv"의 message_pattern과의 대응이다.
 * 패턴표에 없는 원인은 null이다(자동 조치 대상이 아니며 운영자가 확인한다).
 * 대응은 이 파일 한 곳에서만 관리한다.
 */
public enum ErrorKind {
""" + "\n".join(rows) + """

    private final String patternTableKey;

    ErrorKind(String patternTableKey) {
        this.patternTableKey = patternTableKey;
    }

    /** 패턴표 message_pattern. 패턴표에 없는 원인이면 null. */
    public String patternTableKey() {
        return patternTableKey;
    }
}
"""


FILES = {
    "ClientException.java": HEADER + """
/** 클라이언트를 멈추게 하는 오류. 메시지는 client.log와 서버 ERROR 줄에 그대로 남는다. */
public class ClientException extends RuntimeException {
    private final ErrorKind kind;

    public ClientException(ErrorKind kind, String message) {
        super(message);
        this.kind = kind;
    }

    public ClientException(ErrorKind kind, String message, Throwable cause) {
        super(message, cause);
        this.kind = kind;
    }

    public ErrorKind kind() {
        return kind;
    }
}
""",
    "Log.java": HEADER + """
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
""",
    "ClientMain.java": HEADER + """
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
""",
    "ClientConfig.java": HEADER + """
/** client.properties에서 읽은 값. 키 이름은 버전마다 다르다(2.3.1 점 표기, 2.4.x 밑줄 표기). */
public final class ClientConfig {
    private final String serverHost;
    private final int serverPort;
    private final int heartbeatIntervalSec;
    private final String logLevel;
    private final int retryCount;
    private final String internalIpPattern;
    private final String proxyHost;
    private final int proxyPort;

    ClientConfig(String serverHost, int serverPort, int heartbeatIntervalSec, String logLevel, int retryCount,
                 String internalIpPattern, String proxyHost, int proxyPort) {
        this.serverHost = serverHost;
        this.serverPort = serverPort;
        this.heartbeatIntervalSec = heartbeatIntervalSec;
        this.logLevel = logLevel;
        this.retryCount = retryCount;
        this.internalIpPattern = internalIpPattern;
        this.proxyHost = proxyHost;
        this.proxyPort = proxyPort;
    }

    public String serverHost() { return serverHost; }
    public int serverPort() { return serverPort; }
    public int heartbeatIntervalSec() { return heartbeatIntervalSec; }
    public String logLevel() { return logLevel; }
    public int retryCount() { return retryCount; }
    public String internalIpPattern() { return internalIpPattern; }
    public String proxyHost() { return proxyHost; }
    public int proxyPort() { return proxyPort; }
    public boolean viaProxy() { return proxyHost != null; }
}
""",
    "ConfigLoader.java": HEADER + """
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
""",
    "NetworkInterfaceResolver.java": HEADER + """
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.SocketException;
import java.util.Collections;
import java.util.regex.Pattern;

/** internal_ip_pattern에 맞는 네트워크 카드의 IP를 설비망 IP로 고른다. 패턴이 없으면 설비망 IP를 보내지 않는다. */
public final class NetworkInterfaceResolver {
    private final ClientConfig config;

    public NetworkInterfaceResolver(ClientConfig config) {
        this.config = config;
    }

    public String resolveInternalIp() {
        String pattern = config.internalIpPattern();
        if (pattern == null) {
            return null;
        }
        Pattern re = Pattern.compile(pattern);
        try {
            for (NetworkInterface nic : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                for (InetAddress a : Collections.list(nic.getInetAddresses())) {
                    if (re.matcher(a.getHostAddress()).matches()) {
                        return a.getHostAddress();
                    }
                }
            }
        } catch (SocketException e) {
            Log.warn("cannot list network interfaces: " + e.getMessage());
        }
        String key = ClientMain.VERSION.startsWith("2.3.") ? "internal.ip.pattern" : "internal_ip_pattern";
        throw new ClientException(ErrorKind.NIC_PATTERN, String.format("no network interface matches %s=%s", key, pattern));
    }
}
""",
    "LocalControlPort.java": HEADER + """
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
""",
    "ServerConnection.java": HEADER + """
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
""",
    "LicenseClient.java": HEADER + """
import java.net.SocketTimeoutException;
import java.time.LocalDate;

/** 시작할 때 라이선스 서버에 사용 권한을 확인한다. */
public final class LicenseClient {
    private static final int TIMEOUT_SEC = 30;
    private final ClientConfig config;

    public LicenseClient(ClientConfig config) {
        this.config = config;
    }

    public void check() {
        LicenseResponse r;
        try {
            r = LicenseResponse.fetch(config.serverHost(), TIMEOUT_SEC * 1000);
        } catch (SocketTimeoutException e) {
            if (Boolean.getBoolean("abz.license.v1")) {
                // 예전 라이선스 모듈 문구(밀리초 단위)
                throw new ClientException(ErrorKind.LICENSE_TIMEOUT, String.format("license server timeout (%dms)", TIMEOUT_SEC * 1000), e);
            }
            throw new ClientException(ErrorKind.LICENSE_TIMEOUT, String.format("license check timeout after %ds", TIMEOUT_SEC), e);
        }
        LocalDate until = r.validUntil();
        if (until.isBefore(LocalDate.now())) {
            // 시간 초과가 아니라 계약 만료다. 재시작으로 풀리지 않는다.
            throw new ClientException(ErrorKind.LICENSE_EXPIRED, String.format("license expired on %s", until));
        }
        Log.info("license ok until " + until);
    }
}
""",
    "HeartbeatSender.java": HEADER + """
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
""",
    "SpoolWriter.java": HEADER + """
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
            Files.write(dir.resolve("pending.txt"), (line + "\\n").getBytes(StandardCharsets.UTF_8),
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
""",
}


def generate(root=None):
    root = Path(root) if root else paths.ROOT
    out = root / "재료" / "소스"
    if out.exists():
        shutil.rmtree(out)
    d = out / PKG
    d.mkdir(parents=True)
    files = {"ErrorKind.java": _error_kind(), **FILES}
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    _check(out)
    return out


def _check(out):
    """make_world의 모든 메시지가 ErrorKind가 있는 줄의 리터럴로 들어 있는지, 패턴표에 없는 대응이 없는지 확인한다."""
    lits = []
    for f in out.rglob("*.java"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if "ErrorKind." in line:
                lits += LITERAL.findall(line)
    msgs = [m.format(key="retry_count", value="abc") for m in STD_MESSAGES.values()]
    msgs += ["no network interface matches internal_ip_pattern=192.168.9.[0-9]+",
             "identification failed: PC not found on server"]
    msgs += list(VARIANT_MESSAGES) + list(VARIANT_MESSAGES_2) + NEW_MESSAGES + NEW_MESSAGES_2
    missing = [m for m in msgs if not any(re.fullmatch(literal_regex(x), m) for x in lits)]
    if missing:
        raise RuntimeError(f"소스에 없는 메시지: {missing}")
    known = {p for p, *_ in PATTERNS}
    bad = [p for _, p, _ in ERROR_KINDS if p is not None and p not in known]
    if bad:
        raise RuntimeError(f"패턴표에 없는 대응: {bad}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    o = generate()
    files = sorted(o.rglob("*.java"))
    print("소스", len(files), "파일,", sum(len(f.read_text(encoding="utf-8").splitlines()) for f in files), "줄:", o)
