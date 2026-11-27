"""MCP 시험. 주문 서버를 실제 하위 프로세스로 띄워 표준 입출력으로 왕복한다. API 키는 필요 없다.

mcp 패키지가 없으면 건너뛴다.
"""
import json
import types

import pytest

pytest.importorskip("mcp")

import claude_agent  # noqa: E402
import mcp_tools  # noqa: E402
import order_tools  # noqa: E402

B = types.SimpleNamespace


@pytest.fixture
def connection():
    with mcp_tools.McpConnection(mcp_tools.order_server()) as connection:
        yield connection


def test_server_tools_become_agent_tools(connection):
    tools = connection.tools()
    assert [tool.name for tool in tools] == ["get_order", "cancel_order"]
    assert tools[0].description == "주문 번호로 주문 상태와 금액을 조회한다."
    assert tools[0].parameters["required"] == ["order_id"]
    assert all(not tool.strict for tool in tools)
    assert "strict" not in claude_agent.tool_definitions(tools)[0]


def test_calls_run_in_the_server_process(connection, monkeypatch):
    # 이 프로세스의 데이터를 비워도 결과가 같다: Tool은 서버 프로세스에서 실행된다
    monkeypatch.setattr(order_tools, "ORDERS", {})
    tools = connection.tools()

    output, is_error = order_tools.run_tool("get_order", {"order_id": "A-100"}, tools)
    assert not is_error and json.loads(output) == {"order_id": "A-100", "status": "결제완료", "amount": 32000}

    output, is_error = order_tools.run_tool("cancel_order", {"order_id": "A-100"}, tools)
    assert not is_error and json.loads(output)["refund"] == 32000
    output, _ = order_tools.run_tool("get_order", {"order_id": "A-100"}, tools)
    assert json.loads(output)["status"] == "취소"  # 서버가 상태를 기억한다


def test_server_refusals_reach_the_model_with_the_reason(connection):
    output, is_error = order_tools.run_tool("cancel_order", {"order_id": "A-200"}, connection.tools())
    assert is_error and "상태가 '배송중'인 주문은 취소할 수 없다." in output


def test_agent_loop_is_unchanged_with_mcp_tools(connection):
    class FakeClient:
        def __init__(self, script):
            self.script, self.requests, self.messages = script, [], self

        def create(self, **request):
            self.requests.append(json.loads(json.dumps(request, default=vars, ensure_ascii=False)))
            return self.script[len(self.requests) - 1]

    client = FakeClient([
        B(stop_reason="tool_use", content=[
            B(type="tool_use", id="t1", name="cancel_order", input={"order_id": "A-100"}),
            B(type="tool_use", id="t2", name="cancel_order", input={"order_id": "A-200"})]),
        B(stop_reason="end_turn", content=[B(type="text", text="A-100만 취소했습니다.")]),
    ])
    result = claude_agent.run_agent("둘 다 취소", client=client, on_tool=lambda *call: None, tools=connection.tools())

    assert result.ok
    results = client.requests[1]["messages"][-1]["content"]
    assert [(r["tool_use_id"], r["is_error"]) for r in results] == [("t1", False), ("t2", True)]


def test_connection_error_is_raised_instead_of_hanging():
    from mcp import StdioServerParameters

    broken = StdioServerParameters(command=mcp_tools.sys.executable, args=["-c", "raise SystemExit(1)"])
    with pytest.raises(Exception):
        with mcp_tools.McpConnection(broken, timeout=10):
            pass
