"""Claude API(Messages API)로 만든 에이전트 루프.

실행: pip install anthropic, ANTHROPIC_API_KEY 설정 후 python claude_agent.py
"""
import anthropic

from order_tools import MAX_MODEL_CALLS, SYSTEM, TOOL_SPECS, run_tool

MODEL = "claude-opus-5-5"

# Claude의 Tool 정의: 스키마를 input_schema에 넣는다
TOOLS = [
    {"name": s["name"], "description": s["description"], "input_schema": s["parameters"], "strict": True}
    for s in TOOL_SPECS
]


def run_agent(inquiry: str, client=None) -> str:
    client = client or anthropic.Anthropic()  # ANTHROPIC_API_KEY 환경변수를 읽는다
    messages = [{"role": "user", "content": f"<inquiry>\n{inquiry}\n</inquiry>"}]

    for _ in range(MAX_MODEL_CALLS):
        # 시스템 프롬프트는 messages가 아니라 system 인자로 넘긴다
        response = client.messages.create(
            model=MODEL, max_tokens=16000, system=SYSTEM, tools=TOOLS, messages=messages,
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            return f"[중단] stop_reason={response.stop_reason}"

        # 모델의 응답을 통째로 기록에 붙인다 (text만 뽑아 붙이면 tool_use가 사라진다)
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":  # Tool을 더 부르지 않으면 끝
            return "".join(b.text for b in response.content if b.type == "text")

        # 모델이 요청한 Tool을 모두 실행하고, 결과를 user 메시지 하나에 담아 돌려준다
        results = []
        for block in response.content:
            if block.type == "tool_use":
                output, is_error = run_tool(block.name, block.input)  # input은 이미 dict다
                print(f"  [tool] {block.name}({block.input}) -> {output}")
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": output, "is_error": is_error})
        messages.append({"role": "user", "content": results})

    return "[중단] 모델 호출 상한에 걸렸다."


if __name__ == "__main__":
    print(run_agent("A-100이랑 A-200 주문 둘 다 취소해 주세요."))
