"""MCP: Tool을 별도 서버에서 받아 온다.

- 시작할 때 서버에 Tool 목록을 물어 Tool 정의로 바꾼다.
- 모델이 Tool을 요청하면 함수를 직접 부르는 대신 서버에 호출을 보낸다.
- 에이전트 루프는 바뀌지 않는다. tools 자리에 서버에서 받은 Tool을 넣을 뿐이다.

MCP 클라이언트는 비동기(asyncio)다. 샘플의 루프는 동기라서, 연결을 별도 스레드의 이벤트 루프에 두고
호출만 그쪽으로 넘긴다.

실행: pip install mcp 후 python mcp_tools.py   (API 키 필요)
"""
import asyncio
import logging
import sys
import threading
from pathlib import Path

from mcp import Client, StdioServerParameters

from agent_runner import run_agent_from_env
from order_tools import Tool, ToolError

SERVER_SCRIPT = Path(__file__).resolve().parent / "mcp_order_server.py"


class McpConnection:
    """MCP 서버 하나와의 연결. with 블록 안에서 쓴다."""

    def __init__(self, server, timeout: float = 30.0):
        self.server = server  # StdioServerParameters(하위 프로세스) 또는 URL 문자열(원격 서버)
        self.timeout = timeout
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._error: Exception | None = None
        self._thread = threading.Thread(target=self._loop.run_until_complete, args=(self._serve(),), daemon=True)

    async def _serve(self) -> None:
        """연결을 열고, 닫으라는 신호가 올 때까지 유지한다."""
        self._stop = asyncio.Event()
        try:
            async with Client(self.server) as client:
                self._client = client
                self._listed = (await client.list_tools()).tools
                self._ready.set()
                await self._stop.wait()
        except Exception as e:
            self._error = e
            self._ready.set()

    def __enter__(self) -> "McpConnection":
        self._thread.start()
        if not self._ready.wait(self.timeout):
            raise TimeoutError("MCP 서버가 시간 안에 응답하지 않았다.")
        if self._error:
            raise self._error
        return self

    def __exit__(self, *exc) -> None:
        self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(self.timeout)

    def call(self, name: str, args: dict) -> str:
        """서버의 Tool을 부른다. 서버가 오류라고 답하면 ToolError로 바꿔 모델에게 사유가 가게 한다."""
        future = asyncio.run_coroutine_threadsafe(self._client.call_tool(name, args), self._loop)
        result = future.result(self.timeout)
        text = "\n".join(block.text for block in result.content if block.type == "text")
        if result.is_error:
            raise ToolError(text)
        return text

    def tools(self) -> list[Tool]:
        """서버가 알려 준 Tool을 에이전트가 쓰는 Tool로 바꾼다."""
        def remote(name: str):
            return lambda **args: self.call(name, args)

        # 스키마는 서버가 만든 것이라 strict 조건을 맞춘다는 보장이 없다. 인자는 서버가 다시 검사한다
        return [Tool(tool.name, tool.description or "", tool.input_schema, remote(tool.name), strict=False)
                for tool in self._listed]


def order_server() -> StdioServerParameters:
    """주문 서버를 하위 프로세스로 띄우는 방법."""
    return StdioServerParameters(command=sys.executable, args=[str(SERVER_SCRIPT)])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="  [%(name)s] %(message)s")
    with McpConnection(order_server()) as connection:
        result = run_agent_from_env("A-100이랑 A-200 주문 둘 다 취소해 주세요.", tools=connection.tools())
    print(result.text if result.ok else f"[중단] {result.reason}")
