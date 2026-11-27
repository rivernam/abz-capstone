"""주문 Tool을 MCP 서버로 내놓는다. 에이전트와 다른 프로세스에서 돈다.

같은 서버를 여러 에이전트(직접 만든 루프, Claude Code, 사내 챗봇 등)가 함께 쓸 수 있다.
안전 검사는 여전히 Tool 구현(order_tools.py) 안에 있다. 서버를 누가 부르든 같은 검사가 걸린다.

직접 실행할 일은 없다. MCP 클라이언트(mcp_tools.py)가 이 파일을 하위 프로세스로 띄우고 표준 입출력으로 대화한다.
"""
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as McpToolError

import order_tools

server = MCPServer("orders")


def refusal_as_mcp_error(function, order_id: str) -> dict:
    """의도한 거절을 MCP의 ToolError로 바꾼다.

    MCP도 같은 구분을 한다. ToolError는 사유를 모델에게 돌려주고,
    그 밖의 예외는 서버에 기록을 남기고 모델에게는 내용을 숨긴다.
    """
    try:
        return function(order_id)
    except order_tools.ToolError as e:
        raise McpToolError(str(e)) from e


@server.tool()
def get_order(order_id: str) -> dict:
    """주문 번호로 주문 상태와 금액을 조회한다."""
    return refusal_as_mcp_error(order_tools.get_order, order_id)


@server.tool()
def cancel_order(order_id: str) -> dict:
    """주문을 취소한다. 결제완료 상태일 때만 된다. 먼저 get_order로 상태를 확인한다."""
    return refusal_as_mcp_error(order_tools.cancel_order, order_id)


if __name__ == "__main__":
    server.run()  # 표준 입출력으로 요청을 받는다
