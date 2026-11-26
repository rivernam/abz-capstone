/*
 * ABZ 설비 PC 클라이언트 (가상 소스, 과제용)
 */
package com.abz.client;

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
