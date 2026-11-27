# 에이전트 구조와 샘플 코드

LLM 에이전트를 만들 때 쓰는 일반적인 구조를 Python과 Java 샘플로 보여 준다. 같은 에이전트를 Claude API와 OpenAI 호환 API 두 가지로 만들었다.

- 이 문서: 기본 구조(에이전트 루프)와 두 API의 차이
- [운영_요소.md](운영_요소.md): 실제 서비스로 만들 때 더 붙이는 것 네 가지

## 기본 구조

```
사용자 입력
   │
   ▼
┌─ 에이전트 루프 ────────────────────────────────┐
│ ① 모델 호출 (시스템 프롬프트 + Tool 정의 + 대화 기록) │
│ ② 모델이 Tool을 요청했나?                        │
│     아니오 → 최종 답을 돌려주고 끝                  │
│     예 → ③ 코드가 Tool 실행 (안전 검사는 여기)       │
│          ④ 결과를 대화 기록에 붙이고 ①로            │
└──────────────────────────────────────────┘
```

에이전트는 네 부분으로 이루어진다.

| 부분 | 역할 |
|---|---|
| 시스템 프롬프트 | 역할과 규칙을 적는다 |
| Tool 정의 | 모델에게 보여 주는 설명서다. 이름, 설명, 입력 스키마로 이루어진다 |
| Tool 구현 | 실제 동작을 하는 함수다. 안전 검사를 여기에 둔다 |
| 에이전트 루프 | 모델 호출 → Tool 실행 → 결과 전달을 반복하고, 상한에 걸리면 멈춘다 |

모델은 Tool을 직접 실행하지 않는다. "이 Tool을 이 인자로 불러 달라"는 요청만 내놓고, 실행은 우리 코드가 한 뒤 결과를 돌려준다.

## 샘플

주문 조회(`get_order`, 읽기)와 주문 취소(`cancel_order`, 쓰기) Tool을 가진 상담 에이전트다. 문의는 "A-100이랑 A-200 주문 둘 다 취소해 주세요"이고, A-200은 배송중이라 취소 Tool이 거절한다.

| 파일 | 내용 |
|---|---|
| `python/order_tools.py`, `java/.../OrderTools.java` | 시스템 프롬프트, 가상 데이터, Tool 구현, Tool 명세. LLM API에 의존하지 않는다 |
| `python/claude_agent.py`, `java/.../ClaudeOrderAgent.java` | Claude API(Messages API)로 만든 루프 |
| `python/openai_compat_agent.py`, `java/.../OpenAiCompatOrderAgent.java` | OpenAI 호환 API(Chat Completions)로 만든 루프 |

Tool 구현과 명세는 두 루프가 함께 쓴다. API를 바꿀 때 달라지는 것은 루프 파일 하나다.

## 실행

### Python

```powershell
cd python
pip install -r requirements.txt

# Claude API
$env:ANTHROPIC_API_KEY = "<API 키>"
python claude_agent.py

# OpenAI 호환 API
$env:OPENAI_API_KEY = "<API 키>"                  # 키가 필요 없는 로컬 서버는 아무 값
$env:OPENAI_BASE_URL = "http://localhost:11434/v1"  # 서버 주소. 빼면 OpenAI
$env:OPENAI_MODEL = "<서버가 제공하는 모델 이름>"
python openai_compat_agent.py
```

### Java

Java 17 이상과 Maven이 필요하다. 환경변수는 Python과 같다.

```powershell
cd java
mvn -q compile exec:java "-Dexec.mainClass=ClaudeOrderAgent"
mvn -q compile exec:java "-Dexec.mainClass=OpenAiCompatOrderAgent"
```

OpenAI 호환 API는 OpenAI, vLLM, Ollama, LiteLLM처럼 `/v1/chat/completions`를 제공하는 서버에 쓴다. Tool 호출을 지원하는 모델이어야 한다.

## Claude API와 OpenAI 호환 API의 차이

루프의 흐름은 같고, 주고받는 메시지의 모양이 다르다.

| 항목 | Claude API (Messages) | OpenAI 호환 API (Chat Completions) |
|---|---|---|
| 시스템 프롬프트 | `system` 인자 | `messages`의 첫 메시지 (`role: "system"`) |
| Tool 정의 | `{name, description, input_schema}` | `{type: "function", function: {name, description, parameters}}` |
| 모델의 Tool 요청 | `content` 안의 `tool_use` 블록 | 메시지의 `tool_calls` 목록 |
| Tool 인자 | JSON 객체 (`block.input`) | JSON 문자열 (`function.arguments`). 직접 파싱한다 |
| 계속할지 판단 | `stop_reason == "tool_use"` | `tool_calls`가 있는지 |
| Tool 결과 | `user` 메시지 하나에 `tool_result` 블록 여러 개 | 호출마다 `role: "tool"` 메시지 하나 |
| 요청과 결과의 짝 | `tool_use_id` | `tool_call_id` |
| Tool 오류 표시 | `is_error: true` | 표시 칸이 없다. `content`의 문장으로 알린다 |
| 출력이 잘림 | `stop_reason == "max_tokens"` | `finish_reason == "length"` |
| 최대 출력 토큰 | `max_tokens` 필수 | 생략할 수 있다 |

## 코드에서 볼 부분

- **종료 조건**: 모델이 Tool을 더 요청하지 않으면 그 응답이 최종 답이다.
- **모델 응답은 통째로 기록에 붙인다.** 글만 뽑아 붙이면 Tool 요청이 사라져, 뒤따르는 Tool 결과가 짝을 잃고 다음 호출이 실패한다.
- **모델은 한 번에 Tool 여러 개를 요청할 수 있다.** 모두 실행하고 결과를 빠짐없이 돌려준다.
- **Tool 오류는 예외로 프로그램을 멈추지 않고 결과로 돌려준다.** 모델이 사유를 읽고 다음 행동을 정한다. 없는 Tool 이름과 잘못된 인자도 같은 방식으로 처리한다.
- **안전 검사는 Tool 구현 안에 있다.** "배송중이면 취소하지 마라"를 프롬프트에 적는 대신 `cancel_order`가 상태를 확인한다. 프롬프트의 금지 사항은 뚫릴 수 있다.
- **외부 입력은 데이터로 다룬다.** 고객 문의를 `<inquiry>`로 감싸고, 그 안의 지시를 따르지 않는다고 시스템 프롬프트에 적는다.
- **호출 상한을 둔다.** 모델이 Tool을 끝없이 부르는 경우에 대비해 `MAX_MODEL_CALLS`번에서 멈춘다.

## 이 샘플에서 뺀 것

- **SDK가 제공하는 루프**: Claude SDK의 Tool Runner처럼 이 루프를 대신 돌려 주는 도구가 있다. 동작을 이해한 뒤에는 그쪽이 코드가 적다.
- **스트리밍**: 답을 글자 단위로 받아 화면에 바로 보여 주는 방식이다.
- **거절 처리**: Claude API는 안전 분류기가 요청을 거절하면 `stop_reason`이 `refusal`이다. 샘플은 중단만 한다. 서버 측 `fallbacks` 옵션을 켜면 다른 모델로 이어서 처리한다.
- **운영 요소**: 대화 기록 저장, 추적과 비용 집계, 인증과 재시도 큐, 여러 에이전트 구조는 [운영_요소.md](운영_요소.md)에서 다룬다.

## 확인한 범위

- Python: 가짜 클라이언트로 루프 동작을 확인했다. 실제 API는 호출하지 않았다.
- Java: `anthropic-java` 3.3.0과 `openai-java` 4.114.0에 대해 컴파일을 확인했다. 실제 API 호출과 `mvn` 명령은 실행해 보지 않았다.
