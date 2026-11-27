"""OpenAI 호환 API(Chat Completions)로 만든 에이전트 루프.

OpenAI, vLLM, Ollama, LiteLLM처럼 /v1/chat/completions를 제공하는 서버에 쓴다.

실행: pip install openai 후 환경변수를 설정하고 python openai_compat_agent.py
  OPENAI_API_KEY   API 키 (키가 필요 없는 로컬 서버는 아무 값)
  OPENAI_BASE_URL  서버 주소. 예: http://localhost:11434/v1 (빼면 OpenAI)
  OPENAI_MODEL     서버가 제공하는 모델 이름. Tool 호출을 지원하는 모델이어야 한다
"""
import json
import os

from openai import OpenAI

from order_tools import MAX_MODEL_CALLS, SYSTEM, TOOL_SPECS, run_tool

# OpenAI 호환 Tool 정의: {"type": "function", "function": {...}}로 감싸고 스키마를 parameters에 넣는다
TOOLS = [
    {"type": "function",
     "function": {"name": s["name"], "description": s["description"], "parameters": s["parameters"]}}
    for s in TOOL_SPECS
]


def run_agent(inquiry: str, client=None, model=None) -> str:
    client = client or OpenAI()  # OPENAI_API_KEY, OPENAI_BASE_URL 환경변수를 읽는다
    model = model or os.environ["OPENAI_MODEL"]
    # 시스템 프롬프트는 messages의 첫 메시지로 넣는다
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"<inquiry>\n{inquiry}\n</inquiry>"},
    ]

    for _ in range(MAX_MODEL_CALLS):
        completion = client.chat.completions.create(model=model, messages=messages, tools=TOOLS)
        choice = completion.choices[0]
        if choice.finish_reason in ("length", "content_filter"):
            return f"[중단] finish_reason={choice.finish_reason}"
        message = choice.message
        tool_calls = message.tool_calls or []

        # 모델의 응답을 기록에 붙인다. tool_calls를 빼면 뒤따르는 tool 메시지가 짝을 잃는다
        assistant = {"role": "assistant", "content": message.content}
        if tool_calls:
            assistant["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in tool_calls
            ]
        messages.append(assistant)

        if not tool_calls:  # Tool을 더 부르지 않으면 끝
            return message.content or ""

        # Tool 결과는 호출 하나마다 role이 tool인 메시지 하나로 돌려준다
        for call in tool_calls:
            try:
                args = json.loads(call.function.arguments)  # 인자가 JSON 문자열로 온다
                output, _ = run_tool(call.function.name, args)
            except json.JSONDecodeError as e:
                output = f"오류: 인자가 JSON이 아니다: {e}"
            print(f"  [tool] {call.function.name}({call.function.arguments}) -> {output}")
            # 오류 표시 칸이 따로 없으므로 오류 여부는 content의 문장으로 알린다
            messages.append({"role": "tool", "tool_call_id": call.id, "content": output})

    return "[중단] 모델 호출 상한에 걸렸다."


if __name__ == "__main__":
    print(run_agent("A-100이랑 A-200 주문 둘 다 취소해 주세요."))
