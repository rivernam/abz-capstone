/*
 * ABZ 설비 PC 클라이언트 (가상 소스, 과제용)
 */
package com.abz.client;

/**
 * 클라이언트 오류 종류.
 *
 * 생성자 인자는 운영 문서 "오류메시지패턴표.csv"의 message_pattern과의 대응이다.
 * 패턴표에 없는 원인은 null이다(자동 조치 대상이 아니며 운영자가 확인한다).
 * 대응은 이 파일 한 곳에서만 관리한다.
 */
public enum ErrorKind {
    CONNECTION_REFUSED("connection refused"),  // 서버나 프록시가 연결을 거부했다
    CONFIG_PARSE("config parse failed"),       // client.properties 값 해석 실패
    OUT_OF_MEMORY("java.lang.OutOfMemoryError"), // JVM 힙 부족
    LICENSE_TIMEOUT("license check timeout"),  // 라이선스 서버 응답 지연
    PORT_BIND("bind failed"),                  // 로컬 제어 포트 충돌
    HEARTBEAT_SEND("heartbeat send failed"),   // 정상 신호 전송 실패
    NIC_PATTERN("no network interface matches"), // internal_ip_pattern에 맞는 네트워크 카드 없음
    IDENTIFY_FAILED(null),                     // 서버가 PC를 식별하지 못함(기준정보 문제)
    CONFIG_NOT_FOUND(null),                    // 설정 파일 없음
    CONNECTION_RESET(null),                    // 연결 중 끊김(상대가 연결을 닫음)
    TLS_HANDSHAKE(null),                       // TLS 인증서 문제
    LICENSE_EXPIRED(null),                     // 라이선스 만료
    DISK_FULL(null);                           // 디스크 공간 부족

    private final String patternTableKey;

    ErrorKind(String patternTableKey) {
        this.patternTableKey = patternTableKey;
    }

    /** 패턴표 message_pattern. 패턴표에 없는 원인이면 null. */
    public String patternTableKey() {
        return patternTableKey;
    }
}
