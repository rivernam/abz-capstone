"""여러 에이전트로 나누는 구조: 지휘자와 작업자(orchestrator-workers).

지휘자가 일을 독립된 하위 작업으로 나누고, 작업자들이 각자 새 대화에서 동시에 처리한 뒤,
지휘자가 결과를 모아 답을 쓴다.

- 작업자는 지휘자의 대화를 보지 못한다. 넘겨받은 지시문이 아는 것의 전부다.
- 작업자의 긴 중간 과정은 버리고 결과만 지휘자에게 돌아온다. 지휘자의 대화가 짧게 유지된다.
- 모델 호출 수가 (작업자 수 + 2)번으로 늘어난다. 하위 작업이 서로 독립이고 한 대화에 담기 벅찰 때만 쓴다.

실행: python multi_agent.py   (API 키 필요)
"""
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass

from llm import default_complete, parse_json_list

MAX_SUBTASKS = 4  # 작업자 수 상한: 비용이 작업자 수만큼 늘어난다
WORKER_TIMEOUT = 120.0  # 작업자 전체를 기다리는 시간(초). 한 작업자가 멈춰도 전체가 멈추지 않는다

PLAN_SYSTEM = (
    "너는 지휘자다. <task>를 서로 독립적으로 조사할 수 있는 하위 작업 2~4개로 나눈다. "
    "각 하위 작업은 그것만 읽어도 무엇을 해야 하는지 알 수 있게 쓴다. JSON 문자열 배열로만 답한다."
)
WORKER_SYSTEM = "너는 작업자다. 주어진 하위 작업 하나만 처리하고, 결과를 다섯 문장 이내로 정리한다."
SYNTHESIS_SYSTEM = (
    "너는 지휘자다. 작업자들의 결과를 모아 <task>에 대한 답 하나를 쓴다. "
    "결과끼리 어긋나면 그 사실을 밝히고, 실패한 하위 작업은 빠졌다고 알린다."
)


@dataclass(frozen=True)
class WorkerResult:
    subtask: str
    ok: bool
    text: str  # ok이면 결과, 아니면 실패 사유


def plan(complete, task: str) -> list[str]:
    reply = complete(PLAN_SYSTEM, [{"role": "user", "content": f"<task>\n{task}\n</task>"}])
    return parse_json_list(reply.text)[:MAX_SUBTASKS]


def work(complete, subtask: str) -> str:
    """작업자 하나. 새 대화로 시작한다."""
    return complete(WORKER_SYSTEM, [{"role": "user", "content": subtask}]).text


def run_workers(complete, subtasks: list[str], timeout: float = WORKER_TIMEOUT) -> list[WorkerResult]:
    """작업자를 동시에 돌린다. 한 작업자가 실패하거나 늦어도 나머지 결과는 살린다."""
    pool = ThreadPoolExecutor(max_workers=MAX_SUBTASKS)
    futures = [pool.submit(work, complete, subtask) for subtask in subtasks]
    deadline = time.monotonic() + timeout
    results = []
    for subtask, future in zip(subtasks, futures):
        try:
            results.append(WorkerResult(subtask, True, future.result(timeout=max(0.0, deadline - time.monotonic()))))
        except FutureTimeout:
            results.append(WorkerResult(subtask, False, "시간 안에 끝나지 않았다."))
        except Exception as e:
            results.append(WorkerResult(subtask, False, f"{type(e).__name__}: {e}"))
    pool.shutdown(wait=False, cancel_futures=True)  # 늦은 작업자를 기다리지 않는다
    return results


def run(complete, task: str, timeout: float = WORKER_TIMEOUT) -> str:
    subtasks = plan(complete, task)
    if not subtasks:  # 나누지 못하면 혼자 처리한다
        return work(complete, task)

    results = run_workers(complete, subtasks, timeout)
    report = "\n\n".join(
        f"[하위 작업 {i}] {result.subtask}\n[{'결과' if result.ok else '실패'}] {result.text}"
        for i, result in enumerate(results, start=1))
    reply = complete(SYNTHESIS_SYSTEM, [{"role": "user", "content": f"<task>\n{task}\n</task>\n\n{report}"}])
    return reply.text


if __name__ == "__main__":
    print(run(default_complete(), "사내 문의 응대에 에이전트를 도입할 때의 장점, 위험, 필요한 준비를 정리해 줘."))
