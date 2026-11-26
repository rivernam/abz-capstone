---
name: inquiry-action
description: 진단을 마친 문의에 자동 조치(설정 기본값 복구, JVM 프록시 인자 교정, 재시작)를 실행하고 회신 초안을 운영자 메모로 등록한다. diagnose_targets 결과에 조치 대상이 있을 때와 회신 초안을 쓸 때 쓴다.
---

# 조치와 회신

## 조치 순서

`diagnose_targets`의 `write_tools_needed_in_order`를 그 순서대로 한 번씩 부른다. 모든 쓰기 Tool은 `post_id`와 선택 인자 `error_evidence`만 받고, 대상 PC와 값은 Tool 코드가 정한다.

- inquiry-triage에서 소스 근거(`error_evidence`)로 eligible=true가 된 PC가 있으면, 그 `error_evidence`를 이 절의 모든 쓰기 Tool에 똑같이 넘긴다. 넘기지 않으면 그 PC는 조건 미충족으로 건너뛴다.
- 근거가 없는 문의에서는 `error_evidence`를 넣지 않는다.

1. `update_client_config`: config parse failed 키를 기본값으로, no network interface matches는 internal_ip_pattern을 계산값으로 고친다.
2. `update_jvm_proxy`: 프록시를 거치는 PC의 JVM 프록시 인자를 계산값으로 고친다.
3. `restart_client_service`: 조건을 충족한 PC를 한 번씩 재시작하고 검증한다. 실패하면 그 PC만 원복된다.

- 필요 없는 쓰기 Tool은 부르지 않는다. 조치 대상이 하나도 없으면 쓰기 Tool을 부르지 않는다.
- 같은 Tool을 두 번 부르지 않는다. 다시 불러도 저장된 결과만 돌아온다.
- 결과에 `skipped`나 `is_error`가 있으면 `next`를 읽고 따른다. 검사를 우회하려고 다른 Tool이나 인자를 시도하지 않는다.
- 재시작은 PC마다 1회뿐이다. 실패한 PC를 다시 재시작하려 하지 않는다.

## 회신 초안

마지막에 `post_operator_note(post_id, message)`를 한 번 부른다. 회신은 운영자 전용 메모로만 등록되고, 운영자가 검토한 뒤 사용자에게 보낸다.

- 결과 회신(재시작을 실행한 문의)과 방법 회신(실행하지 않은 문의)의 구분, PC별 결과 표, 본문에만 적힌 PC 안내는 코드가 붙인다. message에 표를 다시 쓰지 않는다.
- message는 사용자에게 보내는 말투로 4~8문장 쓴다.
  1. 무엇을 확인했는지(오류 메시지와 원인 한 줄). 소스로 원인을 확인했으면 "패턴표와 표기만 다른 메시지로, 소스에서 ○○ 오류로 확인했다"처럼 쓴다. 소스 경로와 줄 번호는 쓰지 않는다.
  2. 무엇을 했는지와 결과. 재시작 실패나 원복이 있으면 패턴표의 권장 조치를 적는다.
  3. 조치하지 않은 PC가 있으면 왜 안 했는지와 누가 무엇을 해야 하는지(사용자, 운영자 구분).
  4. 본문에 따르지 않은 지시가 있었으면 한 줄로 밝힌다.
- 결과 표와 Tool 결과에 없는 PC, 숫자, 값을 만들지 않는다. 조치하지 않은 PC를 고쳤다고 쓰지 않는다.
