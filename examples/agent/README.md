# 에이전트 구조와 샘플 코드

LLM 에이전트를 만들 때 쓰는 일반적인 구조를 Python과 Java 샘플로 보여 준다. 세 부분으로 이루어진다.

- [기본 구조](#기본-구조): 에이전트 루프. 같은 에이전트를 Claude API와 OpenAI 호환 API 두 가지로 만들었다.
- [Skills와 MCP](#skills와-mcp): 지침과 Tool을 루프 바깥에서 가져오는 방법 두 가지.
- [운영 요소](#운영-요소): 실제 서비스로 만들 때 루프 바깥에 붙이는 것 네 가지.

## 파일

Python은 `python/`, Java는 `java/src/main/java/`에 있다. 스킬 폴더 `skills/`는 두 언어가 함께 쓴다.

| 구분 | 내용 | Python | Java |
|---|---|---|---|
| 기본 구조 | 시스템 프롬프트, 가상 데이터, Tool 등록과 실행 | `order_tools.py` | `OrderTools.java` |
| 기본 구조 | 에이전트 실행 결과 | `agent_result.py` | `AgentResult.java` |
| 기본 구조 | Claude API(Messages API)로 만든 루프 | `claude_agent.py` | `ClaudeOrderAgent.java` |
| 기본 구조 | OpenAI 호환 API(Chat Completions)로 만든 루프 | `openai_compat_agent.py` | `OpenAiCompatOrderAgent.java` |
| Skills와 MCP | 환경변수로 고른 API로 에이전트를 돌린다 | `agent_runner.py` | `AgentRunner.java` |
| Skills와 MCP | 스킬 목록과 스킬 읽기 Tool | `skills.py` | `Skills.java` |
| Skills와 MCP | 주문 Tool을 내놓는 MCP 서버 | `mcp_order_server.py` | `McpOrderServer.java` |
| Skills와 MCP | MCP 서버의 Tool을 받아 오는 클라이언트 | `mcp_tools.py` | `McpTools.java` |
| 운영 요소 | 모델 호출 하나로 두 API를 감싼다 | `llm.py` | `Llm.java` |
| 운영 요소 | 대화 기록과 장기 메모리 | `memory.py` | `MemoryChat.java` |
| 운영 요소 | 추적, 모니터링, 비용 집계 | `tracing.py` | `Tracing.java` |
| 운영 요소 | 인증과 권한, 동시 실행, 재시도 큐 | `service.py` | `AgentService.java` |
| 운영 요소 | 여러 에이전트로 나누는 구조 | `multi_agent.py` | `MultiAgent.java` |
| 시험 | 가짜 클라이언트와 가짜 모델로 돈다 | `tests/` 아래 `test_agents.py`, `test_skills.py`, `test_mcp.py`, `test_ops.py` | `src/test/java/SampleTests.java` (`java/` 기준) |

## 실행

### 환경변수

| API | 환경변수 |
|---|---|
| Claude API | `ANTHROPIC_API_KEY` |
| OpenAI 호환 API | `OPENAI_API_KEY`(키가 필요 없는 로컬 서버는 아무 값), `OPENAI_BASE_URL`(서버 주소. 빼면 OpenAI), `OPENAI_MODEL`(서버가 제공하는 모델 이름) |

OpenAI 호환 API는 OpenAI, vLLM, Ollama, LiteLLM처럼 `/v1/chat/completions`를 제공하는 서버에 쓴다. 기본 구조 샘플은 Tool 호출을 지원하는 모델이어야 한다.

Skills, MCP, 운영 요소 샘플은 `LLM_PROVIDER=openai`이면 OpenAI 호환 API를, 그 외에는 Claude API를 쓴다. `service.py`와 `AgentService`는 가짜 에이전트로 돌아서 API 키가 필요 없다.

### Python

```powershell
cd python
pip install -r requirements.txt

python claude_agent.py           # 기본 구조: Claude API
python openai_compat_agent.py    # 기본 구조: OpenAI 호환 API

python skills.py                 # Skills
python mcp_tools.py              # MCP: 주문 서버를 하위 프로세스로 띄워 쓴다

python memory.py                 # 운영 요소
python tracing.py
python service.py                # API 키 없이 돈다
python multi_agent.py
```

### Java

Java 17 이상과 Maven이 필요하다.

```powershell
cd java
mvn -q compile exec:java "-Dexec.mainClass=ClaudeOrderAgent"
mvn -q compile exec:java "-Dexec.mainClass=OpenAiCompatOrderAgent"

mvn -q compile exec:java "-Dexec.mainClass=Skills"
mvn -q compile exec:java "-Dexec.mainClass=McpTools"       # 주문 서버를 하위 프로세스로 띄워 쓴다

mvn -q compile exec:java "-Dexec.mainClass=MemoryChat"
mvn -q compile exec:java "-Dexec.mainClass=Tracing"
mvn -q compile exec:java "-Dexec.mainClass=AgentService"   # API 키 없이 돈다
mvn -q compile exec:java "-Dexec.mainClass=MultiAgent"
```

운영 요소 샘플의 기록 파일은 실행한 폴더의 `data/`에 생긴다.

### 시험

API 키가 필요 없다.

```powershell
cd python
python -m pytest -q

cd ../java
mvn -q test-compile exec:java "-Dexec.mainClass=SampleTests" "-Dexec.classpathScope=test"
```

Java 시험은 시험 도구 없이 `main`으로 돌고, 실패가 있으면 종료 코드 1로 끝난다. MCP 시험은 주문 서버를 실제 하위 프로세스로 띄워 왕복한다. Python은 `mcp` 패키지가 없으면 MCP 시험만 건너뛴다.

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

### 샘플

주문 조회(`get_order`, 읽기)와 주문 취소(`cancel_order`, 쓰기) Tool을 가진 상담 에이전트다. 문의는 "A-100이랑 A-200 주문 둘 다 취소해 주세요"이고, A-200은 배송중이라 취소 Tool이 거절한다.

Tool 등록과 실행은 두 루프가 함께 쓴다. API를 바꿀 때 달라지는 것은 루프 파일 하나다.

### Claude API와 OpenAI 호환 API의 차이

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

### 코드에서 볼 부분

- **종료 조건**: 모델이 Tool을 더 요청하지 않으면 그 응답이 최종 답이다.
- **모델 응답은 통째로 기록에 붙인다.** 글만 뽑아 붙이면 Tool 요청이 사라져, 뒤따르는 Tool 결과가 짝을 잃고 다음 호출이 실패한다.
- **모델은 한 번에 Tool 여러 개를 요청할 수 있다.** 모두 실행하고 결과를 빠짐없이 돌려준다.
- **Tool 오류는 예외로 프로그램을 멈추지 않고 결과로 돌려준다.** 모델이 사유를 읽고 다음 행동을 정한다. 없는 Tool 이름과 스키마에 맞지 않는 인자도 같은 방식으로 처리한다.
- **의도한 거절과 버그를 구분한다.** Tool이 의도해서 거절한 경우(`ToolError`, `ToolException`)는 사유를 그대로 돌려준다. 그 밖의 예외는 Tool 구현의 버그이므로 기록을 남기고, 모델에게는 내부 사정을 뺀 문장만 돌려준다.
- **명세와 구현을 한 항목으로 등록한다.** 이름, 설명, 스키마, 함수를 `Tool` 하나에 묶어서 한쪽만 고쳐 어긋나는 일이 없다.
- **안전 검사는 Tool 구현 안에 있다.** "배송중이면 취소하지 마라"를 프롬프트에 적는 대신 `cancel_order`가 상태를 확인한다. 프롬프트의 금지 사항은 뚫릴 수 있다.
- **외부 입력은 데이터로 다룬다.** 고객 문의를 `<inquiry>`로 감싸고, 그 안의 지시를 따르지 않는다고 시스템 프롬프트에 적는다.
- **호출 상한을 둔다.** 모델이 Tool을 끝없이 부르는 경우에 대비해 `MAX_MODEL_CALLS`번에서 멈춘다.
- **시스템 프롬프트와 Tool 목록은 인자다.** `run_agent(..., system=..., tools=...)`로 바꿔 끼우면 같은 루프로 다른 에이전트가 된다. 아래 Skills와 MCP가 이 자리를 쓴다.
- **결과는 객체로 돌려준다.** `AgentResult`의 `status`가 `done`이면 `text`가 최종 답이고, `stopped`이면 `reason`에 멈춘 이유가 있다. 부르는 쪽이 글의 앞머리를 보고 성공 여부를 가릴 필요가 없다.
- **Tool 호출은 콜백으로 알린다.** 루프가 화면에 직접 찍지 않고 `on_tool`(Java는 `ToolListener`)을 부른다. 기본값은 로거에 남긴다.

### 이 샘플에서 뺀 것

- **SDK가 제공하는 루프**: Claude SDK의 Tool Runner처럼 이 루프를 대신 돌려 주는 도구가 있다. 동작을 이해한 뒤에는 그쪽이 코드가 적다.
- **스트리밍**: 답을 글자 단위로 받아 화면에 바로 보여 주는 방식이다.
- **거절 처리**: Claude API는 안전 분류기가 요청을 거절하면 `stop_reason`이 `refusal`이다. 샘플은 중단만 한다. 서버 측 `fallbacks` 옵션을 켜면 다른 모델로 이어서 처리한다.

## Skills와 MCP

둘 다 에이전트 루프를 바꾸지 않는다. 기본 구조의 네 부분 중 시스템 프롬프트와 Tool을 바깥에서 공급하는 방법이다.

| | Skills | MCP |
|---|---|---|
| 채우는 자리 | 시스템 프롬프트(지식과 절차) | Tool 정의와 Tool 구현 |
| 실체 | `SKILL.md`가 든 폴더 | 별도 프로세스로 도는 서버 |
| 풀려는 문제 | 지침을 전부 프롬프트에 넣으면 길어지고 비싸진다 | Tool을 에이전트마다 다시 만들어야 한다 |
| 방식 | 이름과 설명만 먼저 보여 주고, 필요할 때 본문을 읽게 한다 | 표준 프로토콜로 Tool을 한 번 만들어 여러 에이전트가 같이 쓴다 |

### Skills

스킬은 폴더 하나이고, `SKILL.md` 맨 위에 이름과 설명을 적는다. 설명에는 무엇을 하는지와 언제 쓰는지를 함께 적는다. 모델이 이 설명만 보고 고르기 때문이다.

```markdown
---
name: order-cancel
description: 고객이 주문 취소나 환불 시점을 물을 때 쓴다. 취소 가능 조건, 환불 안내, 취소가 안 될 때의 대안을 다룬다.
---

# 주문 취소 처리
(절차와 안내 문구)
```

동작은 세 단계다.

1. 시작할 때 폴더마다 머리말만 읽어 스킬 목록을 만든다.
2. 시스템 프롬프트에 목록(이름과 설명)만 붙인다. 본문은 넣지 않는다.
3. 모델이 문의에 맞는 스킬을 고르면 `read_skill` Tool로 본문을 읽고, 그 절차대로 처리한다.

```python
skills = load_skills()
system = SYSTEM + skill_catalog(skills)      # 목록만 붙인다
tools = [*TOOLS, read_skill_tool(skills)]    # Tool 하나를 더한다
for inquiry in INQUIRIES:
    result = run_agent_from_env(inquiry, system=system, tools=tools, on_tool=...)
```

"환불은 영업일 기준 3일 안에 된다"는 내용은 `order-cancel` 스킬 본문에만 있다. 모델은 스킬을 읽어야 이 답을 할 수 있다.

코드에서 볼 부분:

- **`read_skill`은 목록에 있는 이름만 읽는다.** 모델이 넘긴 값을 파일 경로로 쓰지 않으므로 임의의 파일을 읽게 할 수 없다.
- **스킬이 수십 개여도 평소 프롬프트는 짧다.** 늘어나는 것은 스킬 하나당 한 줄이다.
- **머리말은 `키: 값` 줄만 읽는다.** 샘플을 단순하게 하려는 것이고, 실제 스킬 머리말은 YAML이다.

#### Skills 실제 호출 결과

`skills.py`와 `Skills.java`의 `main`은 필요한 스킬이 서로 다른 문의 세 건을 차례로 처리하고, 문의마다 모델이 부른 Tool과 답을 출력한다. 2026-11-27에 Claude API(`claude-opus-5-5`)로 두 언어에서 한 번씩 실행했다. Tool 호출 순서는 두 언어에서 같았다.

| 문의 | 모델이 부른 Tool (순서대로) | 답에 쓰인 스킬 내용 |
|---|---|---|
| A-100 주문 금액이 얼마였죠? | `get_order(A-100)` | 스킬을 읽지 않았다 |
| A-200 주문 언제 도착하나요? 늦어지면 보상이 있나요? | `read_skill(delivery-delay)` → `get_order(A-200)` | 출고 후 영업일 기준 3일 안에 도착, 5일이 지나면 적립금 2,000원, 보상은 상담원이 지급 |
| A-100 주문 취소해 주세요. 환불은 언제 되나요? | `read_skill(order-cancel)` → `get_order(A-100)` → `cancel_order(A-100)` | 환불은 영업일 기준 3일 안에 결제 수단으로 |

- **스킬 선택**: 두 문의 모두 맞는 스킬을 다른 Tool보다 먼저 읽었다.
- **본문 사용**: 환불 기한과 지연 보상은 스킬 본문에만 있는 내용인데 답에 그대로 나왔다. `order-cancel`의 절차(조회 후 취소)도 순서대로 따랐다.
- **불필요한 읽기 없음**: 스킬과 무관한 금액 문의에서는 스킬을 읽지 않고 바로 조회했다.

Java에서 `mvn -q compile exec:java "-Dexec.mainClass=Skills"`로 받은 출력 중 세 번째 문의다.

```
문의: A-100 주문 취소해 주세요. 환불은 언제 되나요?
Tool 호출: read_skill({name=order-cancel}) → get_order({order_id=A-100}) → cancel_order({order_id=A-100})
답: A-100 주문을 취소했습니다.

- **환불 금액:** 32,000원
- **환불 시점:** 오늘 취소됐으므로 영업일 기준 3일 안에 결제하신 수단으로 돌아갑니다.

3영업일이 지나도 환불이 확인되지 않으면 다시 문의해 주세요.
```

문의마다 한 번씩 돌린 결과다. 모델의 답은 실행할 때마다 문장이 달라지고, 스킬 선택도 항상 같다는 보장은 없다.

지침이 적고 항상 필요하면 시스템 프롬프트에 그대로 두는 편이 단순하다. 스킬은 지침이 여러 종류이고 요청마다 필요한 것이 다를 때 쓴다.

### MCP

MCP(Model Context Protocol)는 Tool을 가진 쪽(서버)과 에이전트 쪽(클라이언트)이 주고받는 표준 프로토콜이다. 서버는 로컬 프로세스(표준 입출력)나 원격(HTTP)으로 돈다. 샘플은 표준 입출력을 쓴다.

```
에이전트 프로세스                         MCP 서버 프로세스
┌──────────────────────┐   Tool 목록 요청   ┌────────────────────┐
│ 에이전트 루프          │ ───────────────▶ │ get_order           │
│   tools = 서버의 Tool │ ◀─────────────── │ cancel_order        │
│                      │   Tool 호출       │ (안전 검사는 여기)    │
│ MCP 클라이언트         │ ───────────────▶ │ 주문 데이터          │
└──────────────────────┘ ◀─── 결과 ─────── └────────────────────┘
```

서버는 기본 구조의 Tool 구현을 그대로 내놓는다.

```python
server = MCPServer("orders")

@server.tool()
def cancel_order(order_id: str) -> dict:
    """주문을 취소한다. 결제완료 상태일 때만 된다. 먼저 get_order로 상태를 확인한다."""
    return refusal_as_mcp_error(order_tools.cancel_order, order_id)

server.run()  # 표준 입출력으로 요청을 받는다
```

클라이언트는 서버의 Tool 목록을 에이전트가 쓰는 `Tool`로 바꾼다. Tool의 구현 자리에는 "서버에 호출을 보내는 함수"가 들어간다.

```python
with McpConnection(order_server()) as connection:
    result = run_agent_from_env("A-100이랑 A-200 주문 둘 다 취소해 주세요.", tools=connection.tools())
```

코드에서 볼 부분:

- **Tool은 서버 프로세스에서 실행된다.** 주문 데이터와 안전 검사도 서버에 있다. 서버를 누가 부르든 같은 검사가 걸린다.
- **의도한 거절과 버그의 구분이 MCP에도 있다.** 서버는 거절을 오류 결과(`is_error`)와 사유로 돌려주고, 클라이언트는 그것을 `ToolError`로 바꿔 모델에게 사유가 가게 한다. 버그는 서버에 기록되고 모델에게는 내용이 가지 않는다.
- **서버에서 받은 Tool은 `strict`를 끈다.** 스키마를 서버가 만들기 때문에 엄격 모드의 조건을 맞춘다는 보장이 없다. 인자는 서버가 다시 검사한다.
- **서버의 표준 출력은 프로토콜 전용이다.** 서버 코드에서 표준 출력에 글을 찍으면 통신이 깨진다. 기록은 표준 오류로 보낸다.
- **Python 클라이언트는 비동기다.** 샘플의 루프는 동기라서, `McpConnection`이 연결을 별도 스레드에 두고 호출만 넘긴다. Java SDK에는 동기 클라이언트가 있어 그대로 쓴다.
- **서버와 클라이언트의 언어는 달라도 된다.** Python 클라이언트로 Java 서버(`McpOrderServer`)를 호출해 같은 결과가 나오는 것을 확인했다.

Tool을 에이전트 하나만 쓰면 함수로 두는 편이 단순하다. MCP는 Tool을 여러 에이전트나 여러 팀이 같이 쓸 때, 또는 이미 만들어진 서버를 붙일 때 쓴다. 남이 만든 서버를 붙이면 그 서버의 Tool 설명과 결과가 모델에 그대로 들어가므로, 믿을 수 있는 서버만 연결한다.

### 두 API에서

샘플은 Skills와 MCP를 직접 구현했기 때문에 Claude API와 OpenAI 호환 API에서 똑같이 돈다. Claude API에는 이와 별도로 API 차원의 지원이 있다. 원격 MCP 서버를 요청에 적어 주면 API 쪽에서 연결하는 기능(베타)과, 코드 실행 Tool과 함께 쓰는 Skills 기능이다. 대부분의 OpenAI 호환 서버에는 이런 지원이 없어서 샘플처럼 직접 구현한다.

## 운영 요소

에이전트 루프만으로는 서비스가 되지 않는다. 여러 사람이 계속 쓰는 서비스로 만들 때 루프 바깥에 붙이는 것 네 가지다.

| 요소 | 풀려는 문제 |
|---|---|
| 대화 기록과 장기 메모리 | 모델은 이전 대화를 기억하지 못한다 |
| 추적, 모니터링, 비용 집계 | 무슨 일이 있었고 얼마가 들었는지 모른다 |
| 인증과 권한, 동시 실행, 재시도 큐 | 여러 사람이 동시에 쓰고, 호출은 가끔 실패한다 |
| 여러 에이전트로 나누는 구조 | 일이 한 대화에 담기에 너무 크다 |

샘플은 어떤 LLM API를 쓰는지에 의존하지 않는다. 모델 호출을 `complete(system, messages)` 하나로 맞춘 `llm.py`와 `Llm.java`가 Claude API와 OpenAI 호환 API를 모두 감싼다.

### 1. 대화 기록과 장기 메모리

모델은 상태가 없다. 이전 대화를 기억하는 것처럼 보이려면 매 호출마다 기록을 다시 보내야 한다. 저장할 것은 두 가지다.

| 종류 | 범위 | 내용 | 쓰는 방법 |
|---|---|---|---|
| 대화 기록(단기) | 세션 하나 | 주고받은 메시지 전체 | 다음 호출의 `messages`로 보낸다 |
| 장기 메모리 | 사용자 한 명, 여러 세션 | 이름, 선호, 고정 조건 같은 사실 | 새 세션의 시스템 프롬프트에 넣는다 |

샘플의 `chat`은 한 번 부를 때 다섯 단계를 거친다.

1. 세션 ID로 대화 기록을 읽는다.
2. 사용자 ID로 장기 메모리를 읽어 시스템 프롬프트에 붙인다.
3. 최근 메시지 20개만 골라 모델에 보낸다.
4. 답을 기록에 붙여 저장한다. 저장은 전체를 하고, 보낼 때만 줄인다.
5. 사용자 말에서 기억할 사실을 뽑는 모델 호출을 한 번 더 하고, 새 사실을 메모리에 더한다.

```python
history = sessions.load(session_id)
facts = memories.load(user_id)
system = BASE_SYSTEM + 기억하는_사실(facts)

history.append({"role": "user", "content": text})
reply = complete(system, recent(history))
history.append({"role": "assistant", "content": reply.text})
sessions.save(session_id, history)

try:
    new_facts = extract_facts(complete, text, facts)   # 실패해도 이미 만든 답은 돌려준다
except Exception:
    logger.warning(...)
```

저장소가 지키는 것 두 가지:

- **키 검사**: 세션 ID와 사용자 ID는 외부에서 오는 값이다. 그대로 파일 경로에 쓰면 `../`로 폴더 밖 파일을 건드릴 수 있으므로, 영문자·숫자·`_`·`-` 64자 이내만 받는다.
- **바꿔치기 저장**: 임시 파일에 쓴 뒤 이름을 바꾼다. 쓰다 멈춰도 원본이 깨지지 않는다.

실제 서비스에서 달라지는 점:

- **저장소**: JSON 파일 대신 DB(PostgreSQL, Redis 등)를 쓴다. 인터페이스(`load`, `save`)는 그대로 둔다.
- **긴 대화**: 최근 N개만 남기면 앞 내용을 잃는다. 오래된 부분을 요약해 한 메시지로 바꾸는 방법(압축)을 함께 쓴다.
- **Tool을 쓰는 에이전트**: Tool 요청과 Tool 결과는 짝이라서, 기록을 자를 때 한쪽만 남으면 호출이 실패한다. 짝 단위로 자른다.
- **메모리가 많을 때**: 전부 시스템 프롬프트에 넣을 수 없으면, 질문과 관련 있는 것만 검색해 넣는다.
- **메모리 쓰기 방식**: 샘플처럼 별도 호출로 뽑는 방식과, 에이전트에게 "기억하기" Tool을 주는 방식이 있다.
- **개인정보**: 무엇을 얼마나 오래 저장하는지 정하고, 사용자가 지울 수 있게 한다.

### 2. 추적, 모니터링, 비용 집계

에이전트는 같은 입력에도 매번 다른 경로로 움직인다. 결과만 저장하면 왜 그런 답이 나왔는지 알 수 없으므로, 거친 단계를 모두 남긴다.

| 용어 | 뜻 |
|---|---|
| trace | 요청 하나의 처음부터 끝까지. ID 하나를 갖는다 |
| span | trace 안의 단계 하나. 모델 호출, Tool 실행 등이다. 걸린 시간과 오류를 적는다 |

샘플은 모델 호출 함수를 감싸는 방식을 쓴다. 부르는 쪽 코드를 바꾸지 않아도 호출마다 span이 남는다.

```python
tracer = Tracer("data/traces.jsonl")
complete = tracer.traced(default_complete(), trace_id)   # 감싼 함수를 원래 함수처럼 쓴다

with tracer.span(trace_id, "request", user_id="user-1"):
    complete(system, messages)
```

span 하나는 이런 모양으로 저장된다. `parent_id`는 이 span을 감싼 바깥 span의 ID라서, 어느 단계 안에서 일어난 호출인지 알 수 있다.

```json
{"trace_id": "…", "span_id": "9f2c…", "parent_id": "41ab…", "name": "llm.call",
 "started_at": "2026-11-27T03:35:42.997+00:00", "model": "claude-opus-5-5",
 "input_tokens": 1000, "output_tokens": 500, "cost_usd": 0.014, "duration_ms": 2310}
```

비용은 토큰 수에 단가를 곱한다. 단가표(`PRICES`)는 쓰는 모델의 실제 값으로 채워야 하고, 표에 없는 모델은 0으로 계산된다. 서버가 돌려주는 모델 이름에는 날짜 등이 붙을 수 있어 이름의 앞부분으로 찾는데, 여러 개가 맞으면 가장 긴 이름을 쓴다. `gpt-4o`와 `gpt-4o-mini`가 함께 있을 때 mini에 `gpt-4o` 단가가 적용되지 않게 하기 위해서다.

```
비용 = (입력 토큰 × 입력 단가 + 출력 토큰 × 출력 단가) ÷ 1,000,000
```

`summarize`는 trace별로 모델 호출 수, 토큰, 비용, 오류 수를 합친다. 사용자별, 날짜별 합계도 같은 방식으로 낸다.

실제 서비스에서 달라지는 점:

- **도구**: JSONL 파일 대신 OpenTelemetry로 내보내거나 Langfuse 같은 LLM 전용 추적 도구를 쓴다. 감싸는 자리는 같다.
- **남길 것**: Tool 이름과 인자, Tool 결과, 중단 사유도 span으로 남긴다. 프롬프트와 답의 원문은 개인정보가 들어 있을 수 있으므로 저장 범위를 정한다.
- **볼 지표**: 요청당 비용, 요청당 모델 호출 수, 응답 시간, 오류율, 호출 상한에 걸린 비율.
- **비용 상한**: 집계만 하지 말고 요청 하나나 사용자 한 명이 쓸 수 있는 한도를 정해 넘으면 멈춘다.
- **캐시**: 프롬프트 캐시를 쓰면 캐시에서 읽은 토큰은 단가가 다르다. 그 토큰 수를 따로 적어 계산한다.

### 3. 인증과 권한, 동시 실행, 재시도 큐

| 요소 | 하는 일 | 샘플에서 |
|---|---|---|
| 인증 | 요청을 보낸 사람이 누구인지 확인한다 | `authenticate`: API 키로 사용자를 찾는다 |
| 권한 | 그 사람이 쓸 수 있는 Tool만 실행한다 | `authorize`: 역할별 Tool 목록을 확인한다 |
| 동시 실행 제한 | 한꺼번에 도는 에이전트 수를 제한한다 | 작업자 수 |
| 세션 직렬화 | 같은 세션의 요청은 들어온 순서대로 하나씩 처리한다 | 세션별 대기 줄. 기다리는 요청은 작업자를 차지하지 않는다 |
| 재시도 | 일시적 오류는 간격을 늘려 가며 다시 시도한다 | 타이머가 0.2초, 0.4초 뒤에 다시 큐에 넣는다. 기다리는 동안 작업자는 다른 요청을 처리한다 |
| 실패 보관 | 끝까지 실패한 작업을 버리지 않고 모아 둔다 | `dead` 목록 |
| 멱등 키 | 같은 요청이 두 번 와도 한 번만 처리한다 | 키가 같으면 같은 작업을 돌려준다. 끝난 작업은 정해 둔 시간(기본 1시간)이 지나면 잊는다 |

권한 검사는 Tool을 실행하는 코드에 둔다. 시스템 프롬프트에 "이 사용자는 취소할 수 없다"고 적는 것으로는 막을 수 없다. 모델이 요청했더라도 `authorize`가 거절하면 실행되지 않는다.

같은 세션의 요청 두 개가 동시에 돌면 둘 다 같은 대화 기록을 읽고 각자 저장해서 한쪽이 사라진다. 세션별 대기 줄이 이것을 막는다. 세션이 처리 중이면 새 요청은 줄에 서고, 앞 작업이 끝나면(재시도 중이면 재시도까지 끝나면) 다음 작업이 나간다. 줄이 비면 세션 기록을 지운다.

예상하지 못한 예외(버그)는 기록을 남기고 그 작업만 실패로 끝낸다. 작업이 끝나지 않은 채 남아 기다리는 쪽이 멈추는 일이 없다.

오류는 두 가지로 나눈다.

| 종류 | 예 | 처리 |
|---|---|---|
| 일시적 | 시간 초과, 호출 한도(429), 서버 오류(5xx) | 다시 시도한다 |
| 영구적 | 권한 없음, 잘못된 입력 | 다시 해도 같으므로 바로 실패로 끝낸다 |

멱등 키는 쓰기 Tool이 있는 에이전트에 필요하다. 재시도 때문에 같은 취소나 결제가 두 번 실행되면 안 된다.

샘플을 실행하면 다섯 가지 경우가 차례로 나온다.

```
인증 실패: 알 수 없는 API 키다.
멱등 키가 같으면 같은 job: True
req-1: done, 시도 1회, u-2가 cancel_order 실행
req-2: failed, 시도 1회, PermissionError: viewer 역할은 Tool cancel_order을(를) 쓸 수 없다.
req-3: done, 시도 3회, u-2가 get_order 실행
req-4: dead, 시도 3회, 모델 서버 시간 초과
끝까지 실패한 작업: ['req-4']
```

실제 서비스에서 달라지는 점:

- **인증**: 코드에 적은 키 표 대신 사내 SSO(OIDC) 토큰을 검증하거나, 키의 해시를 DB에 둔다.
- **큐**: 프로세스 안의 큐는 서버가 재시작하면 사라진다. Redis, RabbitMQ, SQS, Kafka 같은 외부 큐를 쓴다.
- **세션 직렬화**: 서버가 여러 대이면 세션별 순서 보장도 서버 밖(Redis, DB, 큐의 파티션 키)에 둔다.
- **재시도 중복**: LLM SDK는 스스로 몇 번 재시도한다. 큐의 재시도와 곱해지지 않게 횟수를 정한다.
- **쓰기 Tool의 멱등성**: 요청 단위 멱등 키와 별개로, Tool 호출마다 키를 만들어 외부 시스템에 넘긴다.
- **사용량 제한**: 사용자별로 분당 요청 수를 제한한다.

### 4. 여러 에이전트로 나누는 구조

가장 흔한 형태는 지휘자와 작업자다. 지휘자가 일을 독립된 하위 작업으로 나누고, 작업자들이 각자 새 대화에서 동시에 처리한 뒤, 지휘자가 결과를 모아 답을 쓴다.

```
           ┌─▶ 작업자 1 (새 대화) ─┐
지휘자: 계획 ─┼─▶ 작업자 2 (새 대화) ─┼─▶ 지휘자: 종합 ─▶ 답
           └─▶ 작업자 3 (새 대화) ─┘
```

```python
subtasks = plan(complete, task)                       # 지휘자: 하위 작업 목록(JSON 배열)
results = run_workers(complete, subtasks, timeout)    # 작업자: 동시에 실행. WorkerResult(subtask, ok, text) 목록
reply = complete(SYNTHESIS_SYSTEM, [...])             # 지휘자: 결과를 모아 답 작성
```

작업자 하나가 실패하거나 정해 둔 시간 안에 끝나지 않아도 나머지 결과는 살린다. 실패한 하위 작업은 `ok=False`와 사유로 지휘자에게 전달되고, 지휘자는 그 부분이 빠졌다고 답에 밝힌다.

나눠서 얻는 것:

- **대화가 짧게 유지된다.** 작업자가 읽은 자료와 중간 과정은 작업자의 대화에만 있고, 지휘자에게는 정리된 결과만 온다.
- **동시에 처리한다.** 하위 작업이 독립이면 걸리는 시간이 줄어든다.
- **역할을 나눈다.** 작업자마다 시스템 프롬프트, Tool 목록, 모델을 다르게 줄 수 있다.

치르는 값:

- **비용**: 모델 호출이 (작업자 수 + 2)번으로 늘어난다.
- **전달 손실**: 작업자는 지휘자의 대화를 보지 못한다. 지시문에 없는 배경은 모른다. 결과도 요약되어 돌아오므로 세부가 빠진다.
- **디버깅**: 어느 에이전트에서 틀렸는지 찾으려면 위 2번의 추적이 필요하다.

그래서 에이전트 하나로 되는 일은 하나로 한다. 여러 개로 나누는 것은 하위 작업이 서로 독립이고, 한 대화에 담기에 자료가 너무 많을 때다.

샘플의 작업자는 모델 호출 한 번이다. 실제로는 작업자 각각이 Tool을 가진 에이전트 루프이고, 지휘자에게는 "작업자에게 맡기기"가 Tool 하나로 주어진다.

다른 형태:

| 형태 | 구조 | 쓰는 경우 |
|---|---|---|
| 분류 후 전달 | 분류기가 문의 종류를 정해 전문 에이전트 하나에 넘긴다 | 문의 종류가 뚜렷이 나뉠 때 |
| 순차 연결 | 앞 에이전트의 출력이 다음 에이전트의 입력이 된다 | 단계가 정해져 있을 때 |
| 작성자와 검토자 | 한 에이전트가 쓰고 다른 에이전트가 기준에 따라 검토한다. 통과할 때까지 반복한다 | 품질 기준이 분명할 때 |

## 확인한 범위

- **Python**: 시험 42개가 통과한다. 두 에이전트 루프는 가짜 클라이언트로, 운영 요소 네 가지는 가짜 모델 함수로 확인했다. MCP는 주문 서버를 실제 하위 프로세스로 띄워 왕복했다(`mcp` 2.3.0). `service.py`는 그대로 실행했다.
- **Java**: Maven 3.9.16으로 빌드했고 `SampleTests` 26개가 통과한다. 두 에이전트 루프는 시험 안에서 가짜 API 서버를 띄워, SDK가 실제로 HTTP 요청을 보내고 응답을 읽는 경로로 끝까지 실행했다. MCP는 `McpOrderServer`를 실제 하위 프로세스로 띄워 왕복했다(MCP Java SDK 2.2.0). `AgentService`는 `mvn` 명령 그대로 실행했다.
- **Java의 JSON 라이브러리**: 샘플 코드는 Jackson 3(3.3.0)을 쓴다. Claude·OpenAI SDK는 내부에서 Jackson 2를 쓰므로 Jackson 2도 클래스 경로에 함께 있다. 두 계열이 같이 쓰는 `jackson-annotations`의 버전은 `pom.xml`의 `jackson-bom`이 맞춘다.
- **실제 API 호출**: 2026-11-27에 API 키가 필요한 `main`을 모두 README의 명령 그대로 한 번씩 실행했고, 12건이 모두 끝까지 돌았다. 모델은 `claude-opus-5-5`다. 결과는 아래 "실제 호출 결과"에 있다.
- **확인하지 않은 것**: OpenAI 호환 API는 Anthropic이 제공하는 OpenAI 호환 주소로만 호출했다. OpenAI나 vLLM·Ollama 같은 다른 호환 서버로는 호출하지 않았다.

### 실제 호출 결과

| 샘플 | Python | Java | 결과 |
|---|---|---|---|
| 기본 루프: Claude API | `claude_agent.py` | `ClaudeOrderAgent` | `get_order` 두 건을 한 번에 요청한 뒤 A-100만 `cancel_order`. A-200은 배송중이라 취소할 수 없다고 안내 |
| 기본 루프: OpenAI 호환 API | `openai_compat_agent.py` | `OpenAiCompatOrderAgent` | 위와 같은 Tool 호출 순서와 같은 취지의 답 |
| Skills | `skills.py` | `Skills` | [Skills 실제 호출 결과](#skills-실제-호출-결과) 참고 |
| MCP | `mcp_tools.py` | `McpTools` | 기본 루프와 같은 Tool 호출 순서. Tool은 하위 프로세스로 뜬 주문 서버에서 실행됐다 |
| 대화 기록과 장기 메모리 | `memory.py` | `MemoryChat` | 같은 세션에서 이름을 기억했고, 새 세션에서도 이름과 "한 문장으로 답하기" 선호를 말했다 |
| 추적과 비용 집계 | `tracing.py` | `Tracing` | 모델 호출 2회, 오류 0. Python은 입력 173·출력 324 토큰에 0.0072달러, Java는 입력 168·출력 240 토큰에 0.0055달러 |
| 여러 에이전트 | `multi_agent.py` | `MultiAgent` | 하위 작업으로 나눠 처리한 뒤 장점·위험·준비 사항을 종합한 답을 냈다. 작업자 결과끼리 어긋난 부분과 검증이 필요한 수치를 답에 따로 밝혔다 |

- **OpenAI 호환 API 설정**: `OPENAI_BASE_URL=https://api.anthropic.com/v1/`, `OPENAI_MODEL=claude-opus-5-5`, `OPENAI_API_KEY`에는 Anthropic API 키를 넣었다. 요청은 `/v1/chat/completions`로 나갔다.
- **기본 루프에서 본 것**: 모델은 배송중인 A-200에 `cancel_order`를 아예 부르지 않았다. 조회 결과와 Tool 설명("결제완료 상태일 때만 된다")을 보고 판단한 것이다. Tool 안의 안전 검사는 이번 실행에서는 걸리지 않았고, 모델이 잘못 요청했을 때를 위한 장치로 남는다.
- 샘플마다 한 번씩 돌린 결과다. 모델의 답은 실행할 때마다 문장이 달라진다.
