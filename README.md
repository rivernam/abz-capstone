# ABZ 클라이언트 운영 지원

설비 PC에서 도는 클라이언트를 운영하는 두 가지 일을 LLM으로 돕는다. 모든 데이터는 가상이다.

| 아이템 | 하는 일 | 형태 | 모델 |
|---|---|---|---|
| ① 일일 리포트 | 서버 실행 로그를 집계해 매일 아침 클라이언트 실행 현황을 HTML 리포트로 발행한다 | 고정 파이프라인 + LLM 판단 1회 | `claude-haiku-5-5` |
| ② 문의 자동 조치 | 게시판 문의를 읽고 대상 PC를 진단해 설정 복구, 프록시 인자 수정, 재시작을 하고 회신 초안을 남긴다 | Tool을 고르는 에이전트 | `claude-sonnet-5-5` |

## 빠른 시작

```powershell
pip install -r requirements.txt
$env:ANTHROPIC_API_KEY = "<발급받은 API 키>"   # 코드와 파일에는 키를 넣지 않는다
python -m demo.web                          # http://127.0.0.1:8765
```

웹 화면에서 할 수 있는 일:

- **일일 리포트**: 날짜(2026-11-21~25)를 골라 리포트를 만든다. LLM 판단 세 가지(처음 보는 오류 분류, 공통 원인 판정, 요약)와 발행된 HTML, LLM과 주고받은 내용을 본다.
- **문의 자동 조치**: 시험 세트 29건 중 하나를 고르거나 문의를 직접 써서 처리한다. 에이전트가 Tool을 고르는 과정이 채팅처럼 실시간으로 보이고, 끝나면 PC 파일 변화와 회신 초안이 나온다.

문의는 매번 가상 세계 복사본(`시연/`)에서 처리하므로 저장소의 원본 데이터는 바뀌지 않는다. 주소 뒤에 `#compose`를 붙이면 새 문의 양식이, `#inquiry=작업ID` 또는 `#report=작업ID`를 붙이면 서버를 켠 뒤 끝난 작업이 다시 열린다.

터미널로 볼 때:

```powershell
python -m demo.run_demo report --date 2026-11-25
python -m demo.run_demo inquiry --posts 3 17 27
```

## 설계 원칙

- **숫자와 대상은 코드가 정한다.** 집계, PC 수, 조치 대상 PC는 Tool과 러너 코드가 계산한다. 모델은 Tool 결과에 없는 숫자나 PC를 만들지 않는다.
- **안전 검사는 Tool 코드 안에 있다**([ADR 0001](docs/adr/0001-안전-검사는-Tool-코드-안에-둔다.md)). 모델이 무엇을 요청해도 쓰기 Tool이 조건(대상 PC, 오류 패턴, 설정 허용 목록, 소스 근거)을 다시 확인한다. 쓰기 전 백업, 재시작 후 검증, 실패 시 원복, 멱등 키가 있다.
- **외부 입력의 지시는 따르지 않는다.** 문의 본문과 로그 메시지는 데이터로만 다룬다.
- **LLM이 실패해도 결과는 나간다.** 리포트는 LLM 호출이 실패해도 숫자 부분을 발행하고, 문의는 호출 상한(모델 15회, Tool 40회)에 걸리면 그때까지의 결과로 처리 중단 회신을 남긴다.
- `CLAUDE.md`와 `.claude/skills/*/SKILL.md`는 Claude Code 작업 규칙이자 서버 러너의 시스템 프롬프트다.

## 아이템 ① 일일 리포트

```
서버 로그 ─▶ summarize_daily_logs (코드: 이상 PC, 식별 실패, 공통 원인 후보)
          ─▶ LLM 1회 (구조화 출력: 메시지 분류, 공통 원인 판정, 요약)
          ─▶ 검증 (프록시 PC가 기준정보에 있는지, 요약의 숫자가 입력에 있는지 → 없으면 요약만 다시 쓰게 함)
          ─▶ publish_report (HTML + 발송 기록, 같은 날짜는 한 번만)
```

```powershell
python -m runner.daily_report --date 2026-11-25     # 날짜를 빼면 전날
```

결과는 `기록/storage/reports/날짜.html`과 `기록/outbox.jsonl`, 실행 기록은 `기록/runs/`에 남는다.

## 아이템 ② 문의 자동 조치

에이전트는 `diagnose_targets`로 대상 PC를 진단하고, 조치 대상이면 쓰기 Tool을 부른 뒤 `post_operator_note`로 회신 초안을 운영자 전용 메모에 남긴다.

| Tool | 종류 | 하는 일 |
|---|---|---|
| `get_post`, `diagnose_targets`, `get_pc_info`, `get_client_state`, `get_server_log`, `get_error_patterns` | 읽기 | 문의, 기준정보, PC 상태, 로그, 패턴표 확인 |
| `find_error_in_source` | 읽기 | 패턴표와 표현이 다른 오류 메시지를 클라이언트 소스에서 찾는다 |
| `update_client_config` | 쓰기 | 설정 기본값 복구 |
| `update_jvm_proxy` | 쓰기 | JVM 프록시 인자 수정([ADR 0004](docs/adr/0004-프록시-인자-값은-같은-사내망-IP를-쓰는-PC들의-값에서-정한다.md)) |
| `restart_client_service` | 쓰기 | 재시작과 재시작 후 검증 |
| `post_operator_note` | 쓰기 | 회신 초안 등록 |

```powershell
python -m tools.board add|list|show|notes|html           # 가상 게시판
python -m runner.inquiry_agent --post-id 3 --root 복사본    # 재료/pc를 바꾸므로 복사본 사용
```

## 평가와 개선 결과

```powershell
python -m eval.score_daily_report --runs 3 --label 이름    # 5일 x 3회
python -m eval.score_inquiries --runs 1 --label 이름       # 문의 29건, --scripted는 LLM 없는 기준선
python -m pytest -q                                       # 43개, 약 3분
```

| 아이템 | 시작 | 마지막 |
|---|---|---|
| ① | 공통 원인 오탐 6번 중 1번, 출처 없는 숫자 3회 실행에서 8개 | 오탐 0번, 발행된 요약의 출처 없는 숫자 0개 |
| ② | 자동 처리율 13/18, 문의 단위 정답률 22/26 | 자동 처리율 19/19, 29/29 (29건 기준) |
| ② | 잘못된 조치 0건 | 0건 유지 |

단계별 내용은 [개선기록_아이템1](docs/개선기록_아이템1.md), [개선기록_아이템2](docs/개선기록_아이템2.md), [개선 과정 기록지 초안](docs/개선과정기록지_초안.md)에 있다. 측정 결과 파일은 `기록/평가/`에 있다.

## 가상 세계

`generator/make_world.py`가 고정 시드로 만든다. 다시 만들어도 같은 결과가 나온다.

| 위치 | 내용 |
|---|---|
| `재료/db/기준정보.sqlite` | PC 300대(5개 공정 × 60), 사내망 IP, 설비망 IP, 공정, 설비 모델, 버전 |
| `재료/서버로그/` | 2026-11-20 ~ 11-26 서버 실행 로그. 날짜마다 함정(엣지 케이스)이 있다 |
| `재료/pc/` | PC별 client.log, client.properties, jvm.args, service.json |
| `재료/문서/` | 오류 메시지 패턴표, 설정 기본값표 |
| `재료/소스/` | 가상 Java 클라이언트 소스(`generator/make_source.py`) |
| `기록/정답표_*.json`, `기록/정답_PC원인.json` | 채점 전용. 에이전트 Tool은 읽지 않는다 |

```powershell
python generator/make_world.py       # 가상 세계
python generator/make_source.py      # 가상 Java 소스
python generator/make_inquiries.py   # 문의 시험 세트
```

## 폴더

```
abzlib/      공용 코드 (경로, 기준정보, 로그 파싱, 진단, 소스 근거 확인, 가상 PC와 재시작)
tools/       Tool 스크립트 (집계, 발행, 게시판, 문의 Tool)
runner/      러너 (일일 리포트 파이프라인, 문의 에이전트 루프, LLM 호출)
eval/        채점
generator/   가상 세계, 소스, 문의 시험 세트 생성
demo/        시연 (웹 화면, 터미널 실행기)
tests/       시험
docs/        구현명세, ADR, 개선 기록
.claude/skills/  daily-report, inquiry-triage, inquiry-action
```

용어는 [GLOSSARY.md](GLOSSARY.md), 구현 기준은 [docs/구현명세.md](docs/구현명세.md), 결정 배경은 [docs/adr/](docs/adr/)를 따른다.
