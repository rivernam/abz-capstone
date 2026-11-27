"""운영 요소 샘플 시험. 가짜 모델 함수를 쓰므로 API 키가 필요 없다."""
import json
import threading
import time

import pytest

import memory
import multi_agent
import service
import tracing
from llm import Reply, parse_json_list


def reply(text, model="claude-opus-5-5", input_tokens=1000, output_tokens=500):
    return Reply(text, model, input_tokens, output_tokens)


# ── llm ──────────────────────────────────────────────────────────────

def test_parse_json_list_tolerates_surrounding_text():
    assert parse_json_list('```json\n["a", "b", 3]\n```') == ["a", "b"]
    assert parse_json_list("없음") == [] and parse_json_list("[깨진") == [] and parse_json_list('{"a": [1]}') == []


# ── memory ───────────────────────────────────────────────────────────

@pytest.fixture
def stores(tmp_path):
    return memory.JsonStore(str(tmp_path / "sessions")), memory.JsonStore(str(tmp_path / "memories"))


def fake_chat_model(calls):
    def complete(system, messages):
        calls.append((system, messages))
        if system == memory.EXTRACT_SYSTEM:
            said = messages[-1]["content"].split("<message>")[1]
            return reply('["이름은 민수"]' if "민수" in said else "[]")
        return reply(f"답 {len(messages)}")
    return complete


def test_history_stays_in_session_and_memory_crosses_sessions(stores):
    sessions, memories = stores
    calls = []
    complete = fake_chat_model(calls)

    memory.chat(complete, sessions, memories, "s-1", "u-1", "내 이름은 민수야")
    assert memory.chat(complete, sessions, memories, "s-1", "u-1", "뭐였지?") == "답 3"  # 앞 대화 2개 + 새 질문
    assert memory.chat(complete, sessions, memories, "s-2", "u-1", "새 세션") == "답 1"

    assert memories.load("u-1") == ["이름은 민수"]
    assert len(sessions.load("s-1")) == 4 and len(sessions.load("s-2")) == 2
    new_session_system = calls[-2][0]
    assert "이름은 민수" in new_session_system  # 새 세션의 시스템 프롬프트에 메모리가 들어간다


@pytest.mark.parametrize("key", ["../../escaped", "a/b", "a\\b", "", "x" * 65, "s 1"])
def test_store_rejects_keys_that_could_leave_the_directory(stores, tmp_path, key):
    sessions, _ = stores
    with pytest.raises(ValueError):
        sessions.save(key, ["x"])
    with pytest.raises(ValueError):
        sessions.load(key)
    assert not list(tmp_path.rglob("escaped.json"))


def test_reply_survives_memory_extraction_failure(stores):
    sessions, memories = stores

    def complete(system, messages):
        if system == memory.EXTRACT_SYSTEM:
            raise RuntimeError("추출 호출 실패")
        return reply("답")

    assert memory.chat(complete, sessions, memories, "s-1", "u-1", "안녕") == "답"
    assert len(sessions.load("s-1")) == 2 and memories.load("u-1") == []


def test_recent_keeps_window_and_starts_with_user():
    pair = [{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}]
    assert len(memory.recent(pair * 12)) == memory.MAX_HISTORY
    odd = [{"role": "assistant", "content": "a"}] + pair * 10  # 21개: 자르면 assistant로 시작한다
    window = memory.recent(odd + [{"role": "user", "content": "u"}])
    assert window[0]["role"] == "user" and len(window) < memory.MAX_HISTORY


# ── tracing ──────────────────────────────────────────────────────────

def test_cost_uses_longest_matching_price(monkeypatch):
    monkeypatch.setattr(tracing, "PRICES", {"gpt-4o": (2.5, 10.0), "gpt-4o-mini": (0.15, 0.6)})
    assert tracing.cost_usd("gpt-4o-mini-2024-07-18", 1_000_000, 0) == pytest.approx(0.15)
    assert tracing.cost_usd("gpt-4o-2024-08-06", 1_000_000, 1_000_000) == pytest.approx(12.5)
    assert tracing.cost_usd("unknown-model", 1_000_000, 1_000_000) == 0.0


def test_spans_record_time_parent_cost_and_errors(tmp_path):
    path = str(tmp_path / "traces.jsonl")
    tracer = tracing.Tracer(path)
    complete = tracer.traced(lambda system, messages: reply("답"), "T1")

    with tracer.span("T1", "request", user_id="u-1"):
        complete("s", [])
        complete("s", [])
    with pytest.raises(ValueError):
        with tracer.span("T1", "broken"):
            raise ValueError("x")

    spans = [json.loads(line) for line in open(path, encoding="utf-8")]
    request = next(s for s in spans if s["name"] == "request")
    calls = [s for s in spans if s["name"] == "llm.call"]
    assert request["parent_id"] is None and request["user_id"] == "u-1"
    assert all(c["parent_id"] == request["span_id"] for c in calls)  # 안쪽 span이 바깥 span을 부모로 적는다
    assert all("started_at" in s and "duration_ms" in s for s in spans)
    assert len({s["span_id"] for s in spans}) == 4
    assert tracing.summarize(path)["T1"] == {
        "llm_calls": 2, "input_tokens": 2000, "output_tokens": 1000, "cost_usd": pytest.approx(0.028), "errors": 1}


def test_concurrent_spans_do_not_corrupt_the_file(tmp_path):
    path = str(tmp_path / "traces.jsonl")
    tracer = tracing.Tracer(path)

    def write_many():
        for _ in range(50):
            with tracer.span("T", "step", note="가" * 200):
                pass

    threads = [threading.Thread(target=write_many) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len([json.loads(line) for line in open(path, encoding="utf-8")]) == 400


# ── service ──────────────────────────────────────────────────────────

def wait_all(jobs, timeout=10):
    for job in jobs:
        assert job.finished.wait(timeout), f"{job.job_id}가 끝나지 않았다"


def test_auth_permission_and_idempotency():
    def agent(user, session_id, text):
        service.authorize(user, "cancel_order")
        return "취소함"

    svc = service.AgentService(agent)
    with pytest.raises(service.AuthError):
        svc.submit("key-wrong", "s-0", "x", "r-0")
    allowed = svc.submit("key-agent", "s-1", "x", "r-1")
    same = svc.submit("key-agent", "s-1", "x", "r-1")
    denied = svc.submit("key-viewer", "s-2", "x", "r-2")
    wait_all([allowed, denied])

    assert same is allowed and allowed.attempts == 1
    assert (allowed.status, allowed.result) == ("done", "취소함")
    assert denied.status == "failed" and "PermissionError" in denied.error and denied.attempts == 1


def test_transient_errors_retry_then_dead_letter():
    failures = {"flaky": 2, "down": 99}

    def agent(user, session_id, text):
        if failures[text] > 0:
            failures[text] -= 1
            raise service.TransientError("시간 초과")
        return "ok"

    svc = service.AgentService(agent, max_attempts=3, base_delay=0.01)
    flaky = svc.submit("key-agent", "s-1", "flaky", "r-1")
    down = svc.submit("key-agent", "s-2", "down", "r-2")
    wait_all([flaky, down])

    assert (flaky.status, flaky.attempts) == ("done", 3)
    assert (down.status, down.attempts) == ("dead", 3) and svc.dead == [down]


def test_retry_wait_does_not_hold_the_worker():
    first_try = threading.Event()

    def agent(user, session_id, text):
        if text == "retry" and not first_try.is_set():
            first_try.set()
            raise service.TransientError("한 번 실패")
        return "ok"

    svc = service.AgentService(agent, workers=1, base_delay=1.0)  # 작업자 하나, 재시도 대기 1초
    retrying = svc.submit("key-agent", "s-1", "retry", "r-1")
    assert first_try.wait(5)
    started = time.monotonic()
    fast = svc.submit("key-agent", "s-2", "fast", "r-2")
    assert fast.finished.wait(5)

    assert time.monotonic() - started < 0.5  # 재시도 대기 1초를 기다리지 않는다
    wait_all([retrying])
    assert retrying.status == "done"


def test_same_session_runs_one_at_a_time_in_order_and_state_is_released():
    running, overlaps, order = set(), [], []

    def agent(user, session_id, text):
        if session_id in running:
            overlaps.append(text)
        running.add(session_id)
        time.sleep(0.02)
        order.append(text)
        running.discard(session_id)
        return text

    svc = service.AgentService(agent, workers=4)
    jobs = [svc.submit("key-agent", "s-1", f"m{i}", f"r-{i}") for i in range(6)]
    wait_all(jobs)

    assert overlaps == [] and order == [f"m{i}" for i in range(6)]
    assert svc.waiting == {}  # 세션 기록이 남지 않는다


def test_unexpected_error_fails_the_job_instead_of_hanging():
    def agent(user, session_id, text):
        raise ZeroDivisionError("버그")

    svc = service.AgentService(agent)
    job = svc.submit("key-agent", "s-1", "x", "r-1")
    wait_all([job])
    assert job.status == "failed" and "ZeroDivisionError" in job.error


def test_finished_jobs_are_forgotten_after_ttl():
    svc = service.AgentService(lambda user, session_id, text: "ok", idempotency_ttl=0.05)
    first = svc.submit("key-agent", "s-1", "x", "r-1")
    wait_all([first])
    time.sleep(0.1)
    second = svc.submit("key-agent", "s-1", "x", "r-1")  # 기억 시간이 지나면 새 작업이다
    wait_all([second])
    assert second is not first and list(svc.jobs) == ["r-1"]


# ── multi_agent ──────────────────────────────────────────────────────

def fake_team(plan_text, slow=()):
    seen = {}

    def complete(system, messages):
        content = messages[-1]["content"]
        if system == multi_agent.PLAN_SYSTEM:
            return reply(plan_text)
        if system == multi_agent.WORKER_SYSTEM:
            if content in slow:
                time.sleep(1.0)
            if content == "b":
                raise RuntimeError("작업자 오류")
            return reply(f"{content} 결과")
        seen["report"] = content
        return reply("종합")
    return complete, seen


def test_workers_run_and_failures_reach_the_lead():
    complete, seen = fake_team('["a", "b", "c"]')
    assert multi_agent.run(complete, "큰 일") == "종합"
    assert "[하위 작업 1] a\n[결과] a 결과" in seen["report"]
    assert "[하위 작업 2] b\n[실패] RuntimeError: 작업자 오류" in seen["report"]
    assert "[하위 작업 3] c\n[결과] c 결과" in seen["report"]


def test_slow_worker_times_out_without_blocking_the_rest():
    complete, _ = fake_team('["a", "slow"]', slow={"slow"})
    started = time.monotonic()
    results = multi_agent.run_workers(complete, ["a", "slow"], timeout=0.2)
    assert time.monotonic() - started < 0.8
    assert [(r.subtask, r.ok) for r in results] == [("a", True), ("slow", False)]


def test_falls_back_to_single_worker_when_plan_is_empty():
    complete, seen = fake_team("나눌 수 없음")
    assert multi_agent.run(complete, "작은 일") == "작은 일 결과" and "report" not in seen


def test_subtask_count_is_capped():
    complete, _ = fake_team(json.dumps([f"t{i}" for i in range(9)]))
    assert len(multi_agent.plan(complete, "x")) == multi_agent.MAX_SUBTASKS
