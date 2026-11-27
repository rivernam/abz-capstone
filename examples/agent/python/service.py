"""인증과 권한, 동시 실행 처리, 재시도 큐.

에이전트를 여러 사람이 쓰는 서비스로 만들 때 루프 바깥에 두는 것들이다.

- 인증: 요청을 보낸 사람이 누구인지 확인한다.
- 권한: 그 사람이 쓸 수 있는 Tool만 실행한다. 프롬프트가 아니라 Tool을 실행하는 코드에서 막는다.
- 동시 실행: 작업자 수로 동시에 도는 에이전트 수를 제한하고, 같은 세션의 요청은 들어온 순서대로 하나씩 처리한다.
- 재시도 큐: 일시적 오류는 간격을 늘려 가며 다시 시도하고, 끝까지 실패하면 따로 모아 둔다.
- 멱등 키: 같은 요청이 두 번 들어와도 한 번만 처리한다.

실행: python service.py   (LLM 없이 돈다. 가짜 에이전트 함수로 동작만 보여 준다)
"""
import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger("agent.service")


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
    finished_at: float | None = None
    finished: threading.Event = field(default_factory=threading.Event)  # 끝나면 켜진다


class AgentService:
    def __init__(self, run_agent, workers: int = 4, max_attempts: int = 3, base_delay: float = 0.2,
                 idempotency_ttl: float = 3600.0):
        self.run_agent = run_agent  # (user, session_id, text) -> str
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.idempotency_ttl = idempotency_ttl  # 끝난 작업을 멱등 키로 기억해 두는 시간(초)
        self.ready: queue.Queue = queue.Queue()  # 지금 실행할 수 있는 작업
        self.jobs: dict[str, Job] = {}  # 멱등 키 -> job
        self.waiting: dict[str, deque[Job]] = {}  # 처리 중인 세션 -> 그 세션에서 차례를 기다리는 작업
        self.dead: list[Job] = []  # 끝까지 실패한 작업. 사람이 확인한다
        self.lock = threading.Lock()
        for _ in range(workers):  # 작업자 수가 동시에 도는 에이전트 수의 상한이다
            threading.Thread(target=self._work, daemon=True).start()

    def submit(self, api_key: str, session_id: str, text: str, idempotency_key: str) -> Job:
        user = authenticate(api_key)  # 인증에 실패하면 큐에 넣지 않는다
        with self.lock:
            self._forget_old_jobs()
            if idempotency_key in self.jobs:  # 같은 요청이 다시 왔다: 새로 만들지 않는다
                return self.jobs[idempotency_key]
            job = self.jobs[idempotency_key] = Job(idempotency_key, user, session_id, text)
            if session_id in self.waiting:  # 이 세션은 처리 중이다: 작업자를 붙잡지 않고 줄만 선다
                self.waiting[session_id].append(job)
            else:
                self.waiting[session_id] = deque()
                self.ready.put(job)
        return job

    def _forget_old_jobs(self) -> None:
        """끝난 지 오래된 작업의 멱등 키를 지운다. 지우지 않으면 기록이 끝없이 쌓인다."""
        cutoff = time.monotonic() - self.idempotency_ttl
        for key in [key for key, job in self.jobs.items() if job.finished_at is not None and job.finished_at < cutoff]:
            del self.jobs[key]

    def _work(self) -> None:
        while True:
            self._attempt(self.ready.get())

    def _attempt(self, job: Job) -> None:
        job.attempts += 1
        try:
            job.result = self.run_agent(job.user, job.session_id, job.text)
            self._finish(job, "done")
        except TransientError as e:
            if job.attempts < self.max_attempts:
                delay = self.base_delay * 2 ** (job.attempts - 1)  # 0.2초, 0.4초, ... 간격을 늘린다
                job.status = "retrying"
                # 기다리는 동안 작업자를 붙잡지 않는다. 타이머가 때가 되면 큐에 다시 넣는다
                timer = threading.Timer(delay, self.ready.put, args=(job,))
                timer.daemon = True
                timer.start()
            else:
                self.dead.append(job)
                self._finish(job, "dead", str(e))
        except Exception as e:  # 권한 없음, 잘못된 입력: 다시 해도 같으므로 재시도하지 않는다
            if not isinstance(e, PermissionError):
                logger.exception("작업 %s 실행 중 예상하지 못한 오류", job.job_id)
            self._finish(job, "failed", f"{type(e).__name__}: {e}")

    def _finish(self, job: Job, status: str, error: str | None = None) -> None:
        job.status, job.error, job.finished_at = status, error, time.monotonic()
        with self.lock:  # 같은 세션에서 기다리던 다음 작업을 내보낸다. 없으면 세션 기록을 지운다
            waiting = self.waiting[job.session_id]
            if waiting:
                self.ready.put(waiting.popleft())
            else:
                del self.waiting[job.session_id]
        job.finished.set()


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
    for job in jobs:
        if not job.finished.wait(timeout=30):
            raise TimeoutError(f"{job.job_id}가 30초 안에 끝나지 않았다.")
    print("멱등 키가 같으면 같은 job:", jobs[0] is jobs[1])
    for job in jobs[1:]:
        print(f"{job.job_id}: {job.status}, 시도 {job.attempts}회, {job.result or job.error}")
    print("끝까지 실패한 작업:", [job.job_id for job in service.dead])
