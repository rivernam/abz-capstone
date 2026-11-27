/** 에이전트 실행 결과. 성공과 중단을 글이 아니라 status로 구분한다. */
public record AgentResult(Status status, String text, String reason) {

    enum Status {
        DONE,   // 모델이 답을 끝냈다. text가 최종 답이다
        STOPPED // 중간에 멈췄다. reason이 이유다
    }

    static AgentResult done(String text) {
        return new AgentResult(Status.DONE, text, null);
    }

    static AgentResult stopped(String reason) {
        return new AgentResult(Status.STOPPED, "", reason);
    }

    boolean ok() {
        return status == Status.DONE;
    }

    /** 화면에 보여 줄 한 줄. */
    String display() {
        return ok() ? text : "[중단] " + reason;
    }
}
