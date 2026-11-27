"""운영 요소 샘플이 함께 쓰는 모델 호출 함수. 글을 넣으면 글이 나오는 가장 단순한 모양이다.

complete(system, messages) -> Reply 하나로 맞춰 두면 나머지 코드는 어떤 API를 쓰는지 몰라도 된다.
messages는 [{"role": "user" | "assistant", "content": "글"}] 목록이다.

  LLM_PROVIDER=openai 이면 OpenAI 호환 API(OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL)
  그 외에는 Claude API(ANTHROPIC_API_KEY)
"""
import json
import os
from dataclasses import dataclass


@dataclass
class Reply:
    text: str
    model: str
    input_tokens: int
    output_tokens: int


def claude_complete(system: str, messages: list[dict], model: str = "claude-opus-5-5") -> Reply:
    import anthropic

    response = anthropic.Anthropic().messages.create(
        model=model, max_tokens=16000, system=system, messages=messages,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("모델이 응답을 거절했다.")
    text = "".join(block.text for block in response.content if block.type == "text")
    return Reply(text, response.model, response.usage.input_tokens, response.usage.output_tokens)


def openai_complete(system: str, messages: list[dict], model: str | None = None) -> Reply:
    from openai import OpenAI

    completion = OpenAI().chat.completions.create(
        model=model or os.environ["OPENAI_MODEL"],
        messages=[{"role": "system", "content": system}, *messages],
    )
    usage = completion.usage  # 호환 서버에 따라 없을 수 있다
    return Reply(
        completion.choices[0].message.content or "",
        completion.model,
        usage.prompt_tokens if usage else 0,
        usage.completion_tokens if usage else 0,
    )


def default_complete():
    return openai_complete if os.environ.get("LLM_PROVIDER") == "openai" else claude_complete


def parse_json_list(text: str) -> list[str]:
    """모델 답에서 JSON 문자열 배열을 꺼낸다. 앞뒤에 다른 글이 붙어도 읽고, 못 읽으면 빈 목록."""
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        return []
    try:
        items = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    return [item for item in items if isinstance(item, str)] if isinstance(items, list) else []
