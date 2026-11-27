"""에이전트 실행 결과. 성공과 중단을 글이 아니라 status로 구분한다."""
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentResult:
    status: str  # "done": 모델이 답을 끝냈다. "stopped": 중간에 멈췄다
    text: str    # done이면 최종 답, stopped이면 빈 문자열
    reason: str | None = None  # stopped인 이유

    @property
    def ok(self) -> bool:
        return self.status == "done"


def done(text: str) -> AgentResult:
    return AgentResult("done", text)


def stopped(reason: str) -> AgentResult:
    return AgentResult("stopped", "", reason)
