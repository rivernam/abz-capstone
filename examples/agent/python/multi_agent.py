"""여러 에이전트로 나누는 구조: 지휘자와 작업자(orchestrator-workers).

지휘자가 일을 독립된 하위 작업으로 나누고, 작업자들이 각자 새 대화에서 동시에 처리한 뒤,
지휘자가 결과를 모아 답을 쓴다.

- 작업자는 지휘자의 대화를 보지 못한다. 넘겨받은 지시문이 아는 것의 전부다.
- 작업자의 긴 중간 과정은 버리고 결과만 지휘자에게 돌아온다. 지휘자의 대화가 짧게 유지된다.
- 모델 호출 수가 (작업자 수 + 2)번으로 늘어난다. 하위 작업이 서로 독립이고 한 대화에 담기 벅찰 때만 쓴다.

실행: python multi_agent.py   (API 키 필요)
"""
from concurrent.futures import ThreadPoolExecutor

from llm import default_complete, parse_json_list

MAX_SUBTASKS = 4  # 작업자 수 상한: 비용이 작업자 수만큼 늘어난다

PLAN_SYSTEM = (
    "너는 지휘자다. <task>를 서로 독립적으로 조사할 수 있는 하위 작업 2~4개로 나눈다. "
    "각 하위 작업은 그것만 읽어도 무엇을 해야 하는지 알 수 있게 쓴다. JSON 문자열 배열로만 답한다."
)
WORKER_SYSTEM = "너는 작업자다. 주어진 하위 작업 하나만 처리하고, 결과를 다섯 문장 이내로 정리한다."
SYNTHESIS_SYSTEM = (
    "너는 지휘자다. 작업자들의 결과를 모아 <task>에 대한 답 하나를 쓴다. "
    "결과끼리 어긋나면 그 사실을 밝히고, 실패한 하위 작업은 빠졌다고 알린다."
)


def plan(complete, task: str) -> list[str]:
    reply = complete(PLAN_SYSTEM, [{"role": "user", "content": f"<task>\n{task}\n</task>"}])
    return parse_json_list(reply.text)[:MAX_SUBTASKS]


def work(complete, subtask: str) -> str:
    """작업자 하나. 새 대화로 시작하고, 실패해도 다른 작업자를 막지 않는다."""
    try:
        return complete(WORKER_SYSTEM, [{"role": "user", "content": subtask}]).text
    except Exception as e:
        return f"[실패] {type(e).__name__}: {e}"


def run(complete, task: str) -> str:
    subtasks = plan(complete, task)
    if not subtasks:  # 나누지 못하면 혼자 처리한다
        return work(complete, task)

    with ThreadPoolExecutor(max_workers=MAX_SUBTASKS) as pool:  # 작업자는 동시에 돈다
        results = list(pool.map(lambda subtask: work(complete, subtask), subtasks))

    report = "\n\n".join(f"[하위 작업 {i}] {subtask}\n[결과] {result}"
                          for i, (subtask, result) in enumerate(zip(subtasks, results), start=1))
    reply = complete(SYNTHESIS_SYSTEM, [{"role": "user", "content": f"<task>\n{task}\n</task>\n\n{report}"}])
    return reply.text


if __name__ == "__main__":
    print(run(default_complete(), "사내 문의 응대에 에이전트를 도입할 때의 장점, 위험, 필요한 준비를 정리해 줘."))
