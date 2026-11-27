"""추적, 모니터링, 비용 집계.

- 추적: 요청 하나(trace)가 거친 단계(span)를 시작 시각, 걸린 시간, 토큰, 오류와 함께 남긴다.
- 비용: 모델 호출마다 토큰 수에 단가를 곱해 span에 적고, 나중에 trace별로 합친다.

여기서는 JSONL 파일에 남긴다. 실제 서비스에서는 같은 자리에 OpenTelemetry나 Langfuse 같은 도구를 붙인다.

실행: python tracing.py   (API 키 필요)
"""
import contextvars
import json
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from llm import default_complete

# 100만 토큰당 달러 (입력, 출력). 쓰는 모델의 실제 단가로 채운다. 표에 없는 모델은 0으로 계산한다.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
}

# 지금 실행 중인 span의 ID. 안쪽 span이 이것을 부모로 적는다 (스레드마다 따로 유지된다)
_current_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_span_id", default=None)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    # 서버가 돌려주는 모델 이름에 날짜 등이 붙을 수 있어 앞부분으로 찾는다.
    # 여러 개가 맞으면 가장 긴 이름을 쓴다 ("gpt-4o"와 "gpt-4o-mini"가 함께 있을 때 mini를 고른다)
    name = max((name for name in PRICES if model.startswith(name)), key=len, default=None)
    input_price, output_price = PRICES[name] if name else (0.0, 0.0)
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


class Tracer:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()  # 여러 스레드가 같은 파일에 쓴다

    @contextmanager
    def span(self, trace_id: str, name: str, **attrs):
        """with 블록 하나가 span 하나다. 블록 안에서 record에 값을 더 적을 수 있다."""
        record = {"trace_id": trace_id, "span_id": uuid.uuid4().hex[:16], "parent_id": _current_span_id.get(),
                  "name": name, "started_at": datetime.now(timezone.utc).isoformat(), **attrs}
        token = _current_span_id.set(record["span_id"])
        started = time.perf_counter()
        try:
            yield record
        except Exception as e:
            record["error"] = f"{type(e).__name__}: {e}"
            raise
        finally:
            _current_span_id.reset(token)
            record["duration_ms"] = round((time.perf_counter() - started) * 1000)
            with self._write_lock, self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def traced(self, complete, trace_id: str):
        """모델 호출 함수를 감싼다. 부르는 쪽 코드는 바꾸지 않고 호출마다 span이 남는다."""
        def wrapper(system, messages):
            with self.span(trace_id, "llm.call") as record:
                reply = complete(system, messages)
                record.update(model=reply.model, input_tokens=reply.input_tokens,
                              output_tokens=reply.output_tokens,
                              cost_usd=cost_usd(reply.model, reply.input_tokens, reply.output_tokens))
                return reply
        return wrapper


def summarize(path: str) -> dict:
    """trace별 합계: 모델 호출 수, 토큰, 비용, 오류 수."""
    totals: dict[str, dict] = {}
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            span = json.loads(line)
            total = totals.setdefault(span["trace_id"], {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0,
                                                         "cost_usd": 0.0, "errors": 0})
            if span["name"] == "llm.call":
                total["llm_calls"] += 1
                total["input_tokens"] += span.get("input_tokens", 0)
                total["output_tokens"] += span.get("output_tokens", 0)
                total["cost_usd"] += span.get("cost_usd", 0.0)
            if "error" in span:
                total["errors"] += 1
    return totals


if __name__ == "__main__":
    tracer = Tracer("data/traces.jsonl")
    trace_id = uuid.uuid4().hex  # 요청 하나에 ID 하나. 로그와 응답에도 같은 ID를 남기면 찾기 쉽다
    complete = tracer.traced(default_complete(), trace_id)

    with tracer.span(trace_id, "request", user_id="user-1"):
        first = complete("한 문장으로 답한다.", [{"role": "user", "content": "에이전트 추적이 왜 필요해?"}])
        complete("한 단어로 답한다.", [{"role": "user", "content": f"이 문장의 핵심어는? {first.text}"}])

    print(json.dumps(summarize("data/traces.jsonl")[trace_id], ensure_ascii=False, indent=1))
