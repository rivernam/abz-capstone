"""기본 에이전트 루프와 Tool 시험. 가짜 클라이언트를 쓰므로 API 키가 필요 없다.

실행: python/ 폴더에서 python -m pytest -q
"""
import json
import logging
import types

import pytest

import claude_agent
import openai_compat_agent
import order_tools

B = types.SimpleNamespace


@pytest.fixture(autouse=True)
def fresh_orders(monkeypatch):
    monkeypatch.setattr(order_tools, "ORDERS", {
        "A-100": {"status": "결제완료", "amount": 32000},
        "A-200": {"status": "배송중", "amount": 15000},
    })


class FakeClient:
    """정해 둔 응답을 차례로 돌려주고, 받은 요청을 적어 둔다. 두 SDK의 호출 경로를 모두 흉내 낸다."""

    def __init__(self, script):
        self.script, self.requests = script, []
        self.messages = self                      # client.messages.create
        self.chat = B(completions=self)           # client.chat.completions.create

    def create(self, **request):
        self.requests.append(json.loads(json.dumps(request, default=vars, ensure_ascii=False)))
        return self.script[min(len(self.requests), len(self.script)) - 1]


def claude_tool_use(*calls):
    return B(stop_reason="tool_use", content=[B(type="tool_use", id=i, name=n, input=a) for i, n, a in calls])


def openai_tool_calls(*calls):
    return B(choices=[B(finish_reason="tool_calls", message=B(content=None, tool_calls=[
        B(id=i, function=B(name=n, arguments=a)) for i, n, a in calls]))])


def test_run_tool_refusals_are_reported_to_the_model():
    assert order_tools.run_tool("get_order", {"order_id": "A-100"}) == (
        '{"order_id": "A-100", "status": "결제완료", "amount": 32000}', False)
    assert order_tools.run_tool("cancel_order", {"order_id": "A-200"}) == (
        "오류: 상태가 '배송중'인 주문은 취소할 수 없다.", True)
    assert order_tools.run_tool("nope", {})[1] is True
    assert order_tools.run_tool("get_order", {})[0] == "오류: 인자 order_id가 없다."
    assert order_tools.run_tool("get_order", {"order_id": 1})[1] is True
    assert order_tools.run_tool("get_order", {"order_id": "A-100", "x": "y"})[1] is True
    assert order_tools.run_tool("get_order", ["A-100"])[1] is True


def test_run_tool_logs_bugs_and_hides_details(caplog):
    def broken(order_id):
        raise KeyError("secret-internal-detail")

    tool = order_tools.Tool("get_order", "d", order_tools.ORDER_ID_SCHEMA, broken)
    with caplog.at_level(logging.ERROR, logger="agent.tools"):
        output, is_error = order_tools.run_tool("get_order", {"order_id": "A-100"}, [tool])
    assert is_error and "secret-internal-detail" not in output
    assert "secret-internal-detail" in caplog.text  # 기록에는 남는다


def test_claude_agent_runs_tools_and_returns_done():
    client = FakeClient([
        claude_tool_use(("t1", "cancel_order", {"order_id": "A-100"}), ("t2", "cancel_order", {"order_id": "A-200"})),
        B(stop_reason="end_turn", content=[B(type="text", text="A-100만 취소했습니다.")]),
    ])
    seen = []
    result = claude_agent.run_agent("둘 다 취소", client=client, on_tool=lambda *call: seen.append(call))

    assert result.ok and result.text == "A-100만 취소했습니다."
    assert order_tools.ORDERS["A-100"]["status"] == "취소"
    assert order_tools.ORDERS["A-200"]["status"] == "배송중"
    tool_results = client.requests[1]["messages"][-1]
    assert tool_results["role"] == "user"  # 결과 두 개가 user 메시지 하나에 담긴다
    assert [(r["tool_use_id"], r["is_error"]) for r in tool_results["content"]] == [("t1", False), ("t2", True)]
    assert [(name, is_error) for name, _, _, is_error in seen] == [("cancel_order", False), ("cancel_order", True)]


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_claude_agent_reports_stop_as_status(stop_reason):
    result = claude_agent.run_agent("x", client=FakeClient([B(stop_reason=stop_reason, content=[])]))
    assert not result.ok and result.text == "" and stop_reason in result.reason


def test_claude_agent_stops_at_call_limit():
    client = FakeClient([claude_tool_use(("t", "get_order", {"order_id": "A-100"}))])
    result = claude_agent.run_agent("x", client=client, on_tool=lambda *call: None)
    assert result.status == "stopped" and len(client.requests) == order_tools.MAX_MODEL_CALLS


def test_openai_agent_runs_tools_and_returns_done():
    client = FakeClient([
        openai_tool_calls(("c1", "cancel_order", '{"order_id": "A-100"}'), ("c2", "get_order", "{bad json")),
        B(choices=[B(finish_reason="stop", message=B(content="처리했습니다.", tool_calls=None))]),
    ])
    seen = []
    result = openai_compat_agent.run_agent("취소", client=client, model="m", on_tool=lambda *call: seen.append(call))

    assert result.ok and result.text == "처리했습니다."
    assert order_tools.ORDERS["A-100"]["status"] == "취소"
    sent = client.requests[1]["messages"]
    assert sent[0]["role"] == "system"
    assert [c["id"] for c in sent[2]["tool_calls"]] == ["c1", "c2"]  # assistant 메시지에 tool_calls가 남는다
    assert [(m["role"], m["tool_call_id"]) for m in sent[3:]] == [("tool", "c1"), ("tool", "c2")]
    assert sent[4]["content"].startswith("오류: 인자가 JSON이 아니다")
    assert [is_error for *_, is_error in seen] == [False, True]


def test_openai_agent_reports_stop_as_status():
    client = FakeClient([B(choices=[B(finish_reason="length", message=B(content="잘린 글", tool_calls=None))])])
    result = openai_compat_agent.run_agent("x", client=client, model="m")
    assert not result.ok and "length" in result.reason
