"""인증과 권한, 동시 실행 처리, 재시도 큐.

에이전트를 여러 사람이 쓰는 서비스로 만들 때 루프 바깥에 두는 것들이다.

- 인증: 요청을 보낸 사람이 누구인지 확인한다.
- 권한: 그 사람이 쓸 수 있는 Tool만 실행한다. 프롬프트가 아니라 Tool을 실행하는 코드에서 막는다.
- 동시 실행: 작업자 수로 동시에 도는 에이전트 수를 제한하고, 같은 세션의 요청은 한 번에 하나만 처리한다.
- 재시도 큐: 일시적 오류는 간격을 늘려 가며 다시 시도하고, 끝까지 실패하면 따로 모아 둔다.
- 멱등 키: 같은 요청이 두 번 들어와도 한 번만 처리한다.

실행: python service.py   (LLM 없이 돈다. 가짜 에이전트 함수로 동작만 보여 준다)
"""
import itertools
import queue
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    user_id: str
    role: str


# 시연용 표. 실제로는 키의 해시를 DB에 두거나 사내 SSO(OIDC) 토큰을 검증한다
API_KEYS = {"key-viewer": User("u-1", "viewer"), "key-agent": User("u-2", "agent")}
ROLE_TOOLS = {"viewer": {"get_order"}, "agent": {"get_order", "cancel_order"}}


class AuthError(Exception):
    pass


class TransientError(Exception):
    """다시 시도하면 될 수 있는 오류: 시간 초과, 호출 한도(429), 서버 오류(5xx)."""


def authenticate(api_key: str) -> User:
    if api_key not in API_KEYS:
        raise AuthError("알 수 없는 API 키다.")
    return API_KEYS[api_key]


def authorize(user: User, tool_name: str) -> None:
    """Tool을 실행하기 직전에 부른다. 모델이 요청했더라도 권한이 없으면 실행하지 않는다."""
    if tool_name not in ROLE_TOOLS[user.role]:
        raise PermissionError(f"{user.role} 역할은 Tool {tool_name}을(를) 쓸 수 없다.")


@dataclass
class Job:
    job_id: str
    user: User
    session_id: str
    text: str
    attempts: int = 0
    status: str = "queued"  # queued -> done | retrying -> ... | failed | dead
    result: str | None = None
    error: str | None = None


class AgentService:
    def __init__(self, run_agent, workers: int = 4, max_attempts: int = 3, base_delay: float = 0.2):
        self.run_agent = run_agent  # (user, session_id, text) -> str
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.queue: queue.PriorityQueue = queue.PriorityQueue()  # (실행 가능 시각, 순번, job)
        self.sequence = itertools.count()
        self.jobs: dict[str, Job] = {}  # 멱등 키 -> job
        self.session_locks: dict[str, threading.Lock] = {}
        self.dead: list[Job] = []  # 끝까지 실패한 작업. 사람이 확인한다
        self.lock = threading.Lock()
        for _ in range(workers):  # 작업자 수가 동시에 도는 에이전트 수의 상한이다
            threading.Thread(target=self._work, daemon=True).start()

    def submit(self, api_key: str, session_id: str, text: str, idempotency_key: str) -> Job:
        user = authenticate(api_key)  # 인증에 실패하면 큐에 넣지 않는다
        with self.lock:
            if idempotency_key in self.jobs:  # 같은 요청이 다시 왔다: 새로 만들지 않는다
                return self.jobs[idempotency_key]
            job = self.jobs[idempotency_key] = Job(idempotency_key, user, session_id, text)
            self.session_locks.setdefault(session_id, threading.Lock())
        self.queue.put((time.monotonic(), next(self.sequence), job))
        return job

    def wait(self) -> None:
        self.queue.join()

    def _work(self) -> None:
        while True:
            not_before, _, job = self.queue.get()
            time.sleep(max(0.0, not_before - time.monotonic()))  # 재시도 대기
            with self.session_locks[job.session_id]:  # 같은 세션은 한 번에 하나만
                self._attempt(job)
            self.queue.task_done()

    def _attempt(self, job: Job) -> None:
        job.attempts += 1
        try:
            job.result = self.run_agent(job.user, job.session_id, job.text)
            job.status = "done"
        except TransientError as e:
            if job.attempts < self.max_attempts:
                delay = self.base_delay * 2 ** (job.attempts - 1)  # 0.2초, 0.4초, ... 간격을 늘린다
                job.status = "retrying"
                self.queue.put((time.monotonic() + delay, next(self.sequence), job))
            else:
                job.status, job.error = "dead", str(e)
                self.dead.append(job)
        except Exception as e:  # 권한 없음, 잘못된 입력: 다시 해도 같으므로 재시도하지 않는다
            job.status, job.error = "failed", f"{type(e).__name__}: {e}"


if __name__ == "__main__":
    failures_left = {"불안정": 2, "계속 실패": 99}

    def fake_agent(user: User, session_id: str, text: str) -> str:
        """에이전트 루프 자리. 실제로는 여기서 run_agent를 부르고, Tool 실행 직전에 authorize를 부른다."""
        for word in failures_left:
            if word in text and failures_left[word] > 0:
                failures_left[word] -= 1
                raise TransientError("모델 서버 시간 초과")
        tool = "cancel_order" if "취소" in text else "get_order"
        authorize(user, tool)
        return f"{user.user_id}가 {tool} 실행"

    service = AgentService(fake_agent)
    try:
        service.submit("key-wrong", "s-0", "조회", "req-0")
    except AuthError as e:
        print("인증 실패:", e)

    jobs = [
        service.submit("key-agent", "s-1", "A-100 취소", "req-1"),
        service.submit("key-agent", "s-1", "A-100 취소", "req-1"),  # 같은 멱등 키: 위와 같은 job
        service.submit("key-viewer", "s-2", "A-100 취소", "req-2"),  # 권한 없음
        service.submit("key-agent", "s-3", "불안정한 조회", "req-3"),  # 두 번 실패하고 세 번째에 성공
        service.submit("key-agent", "s-4", "계속 실패하는 조회", "req-4"),  # 끝까지 실패
    ]
    service.wait()
    print("멱등 키가 같으면 같은 job:", jobs[0] is jobs[1])
    for job in jobs[1:]:
        print(f"{job.job_id}: {job.status}, 시도 {job.attempts}회, {job.result or job.error}")
    print("끝까지 실패한 작업:", [job.job_id for job in service.dead])
