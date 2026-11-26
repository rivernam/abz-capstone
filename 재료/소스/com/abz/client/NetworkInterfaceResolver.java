/*
 * ABZ 설비 PC 클라이언트 (가상 소스, 과제용)
 */
package com.abz.client;

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
