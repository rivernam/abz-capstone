/*
 * ABZ 설비 PC 클라이언트 (가상 소스, 과제용)
 */
package com.abz.client;

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
