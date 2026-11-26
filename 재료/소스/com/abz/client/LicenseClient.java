/*
 * ABZ 설비 PC 클라이언트 (가상 소스, 과제용)
 */
package com.abz.client;

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
