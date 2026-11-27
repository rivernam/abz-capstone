"""아이템 ② 시험: 대상 파서, 게시판, 프록시 계산, 쓰기 Tool 안전 검사, 가상 재시작, 에이전트 루프 상한.

저장소의 재료/는 쓰지 않는다. tmp 경로에 generate()로 세계를 만들고, 쓰는 시험은 그 복사본을 쓴다.
"""
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from abzlib import history, inquiry
from abzlib import virtual_pc as vpc
from abzlib.targets import parse_pc_field
from abzlib.world import World
from eval.score_inquiries import scripted_agent
from generator import make_inquiries, make_source
from generator.make_world import generate
from runner import inquiry_agent
from tools import board
from tools import inquiry_tools as T

CLOCK = lambda: datetime(2026, 11, 26, 9, 0, 0)


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    root = tmp_path_factory.mktemp("base")
    generate(root)
    make_source.generate(root)
    posts, answers = make_inquiries.generate(root)
    return {"root": root, "posts": posts, "answers": answers,
            "hidden": json.loads((root / "기록" / "정답_PC원인.json").read_text(encoding="utf-8"))}


@pytest.fixture
def world(base, tmp_path):
    dst = tmp_path / "w"
    shutil.copytree(base["root"] / "재료", dst / "재료")
    (dst / "기록").mkdir()
    shutil.copy2(base["root"] / "기록" / "정답_PC원인.json", dst / "기록" / "정답_PC원인.json")
    return World(dst, clock=CLOCK)


def add(world, pcs, body="확인 부탁"):
    return board.create_post(world, "시험", "제목", pcs, body)


def test_parse_pc_field():
    assert parse_pc_field("PC-A0101~0103")["targets"] == ["PC-A0101", "PC-A0102", "PC-A0103"]
    assert parse_pc_field("PC-A0101~PC-A0102, pc-b0105")["targets"] == ["PC-A0101", "PC-A0102", "PC-B0105"]
    p = parse_pc_field("PC-A0101~0125")
    assert len(p["targets"]) == 20 and p["over_limit"] == [f"PC-A{n:04d}" for n in range(121, 126)]
    p = parse_pc_field("PC-A0105~0101, PC-A0101~PC-B0102, 아무거나, PC-A0101~9999")
    assert p["targets"] == [] and len(p["invalid"]) == 4
    p = parse_pc_field("PC-A0101, PC-A0101", body="PC-A0102도 같은 증상, PC-A0101도")
    assert p["targets"] == ["PC-A0101"] and p["body_only"] == ["PC-A0102"]


def test_board_visibility_is_fixed(world):
    pid = add(world, "PC-A0101")
    n = board.add_operator_note(world, pid, "초안", "k1")
    assert n["visibility"] == "operator" and not n["replayed"]
    assert board.add_operator_note(world, pid, "다른 초안", "k1")["replayed"]
    con = sqlite3.connect(world.board_db)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO notes (post_id, visibility, body, created_at) VALUES (?, 'public', 'x', 'now')", (pid,))
    con.close()
    out = Path(board.render_html(world))
    assert "초안" in (out / "operator" / f"post_{pid}.html").read_text(encoding="utf-8")
    assert "초안" not in (out / "public" / f"post_{pid}.html").read_text(encoding="utf-8")


def test_proxy_plans_match_hidden(base):
    w = World(base["root"])
    ctx = inquiry.Context(w)
    for pc, h in base["hidden"]["pcs"].items():
        plan = inquiry.plan_pc(ctx, pc)
        if h["cause"] in ("proxy_missing", "proxy_wrong"):
            assert plan["action"] == {"type": "jvm_proxy", "value": h["expected_proxy"], "current": plan["action"]["current"]}, pc
        elif h["cause"] == "pattern_mismatch":
            assert plan["action"]["value"] == h["expected_pattern"], pc
        elif h["cause"] in ("proxy_uncomputable", "proxy_down", "oom", "bind", "identify_fail", "not_running"):
            assert not plan["eligible"], pc


def test_scripted_agent_matches_answers(base, world):
    """진단 결과대로 Tool을 부르면 정답과 같아야 한다(코드 경로와 정답의 일관성). 상태가 겹치는 #17은 따로 돈다.
    개선 단계 문의(#21~26)는 소스 근거 없이는 표기 변형도 조치하지 않으므로 전부 조건 미충족이어야 한다."""
    for p in base["posts"]:
        if p["post_id"] == 17:
            continue
        board.create_post(world, p["author"], p["title"], p["pc_field"], p["body"], p["created_at"], post_id=p["post_id"])
        scripted_agent(world, p["post_id"])
        parsed = parse_pc_field(p["pc_field"], p["body"])
        got = {r["pc_id"]: r["result"] for r in inquiry.result_table(world, board.get_post(world, p["post_id"]), parsed)}
        ans = base["answers"][str(p["post_id"])]
        if p["post_id"] > 20:
            assert set(got.values()) == {inquiry.NOT_MET} and not history.rows(world, p["post_id"]), p["post_id"]
            continue
        assert got == ans["expected"], p["post_id"]
        assert ans["reply_type"] in board.list_notes(world, p["post_id"])[0]["body"].splitlines()[0]
    acted = {pc for a in base["answers"].values() for pc in a["acted_pcs"]}
    assert {h["pc_id"] for h in history.rows(world)} <= acted


def test_restart_requires_fix_first_and_is_idempotent(base, world):
    pc = base["hidden"]["scenarios"]["op_config"][0]
    pid = add(world, pc)
    r = T.restart_client_service(world, pid)
    assert "skipped" in r["results"][0] and "update_client_config" in r["results"][0]["next"]
    assert T.update_client_config(world, pid)["applied"] == 1
    assert T.update_client_config(world, pid)["results"][0]["replayed"]
    assert T.restart_client_service(world, pid)["results"][0]["result"] == "success"
    log = (world.pc_dir / pc / "client.log").read_text(encoding="utf-8")
    again = T.restart_client_service(world, pid)
    assert again["results"][0]["replayed"] and (world.pc_dir / pc / "client.log").read_text(encoding="utf-8") == log
    assert vpc.read_service(world, pc)["status"] == "running"


def test_failed_restart_rolls_back_only_that_pc(base, world):
    a, b = base["hidden"]["scenarios"]["op_config"][:2]
    hidden = json.loads(world.hidden_path.read_text(encoding="utf-8"))
    hidden["pcs"][a]["restart_succeeds_if"] = "never"
    world.hidden_path.write_text(json.dumps(hidden), encoding="utf-8")
    orig = (world.pc_dir / a / "client.properties").read_text(encoding="utf-8")
    pid = add(world, f"{a}, {b}")
    T.update_client_config(world, pid)
    r = {x["pc_id"]: x for x in T.restart_client_service(world, pid)["results"]}
    assert r[a]["result"] == "rolled_back" and r[a]["restored"] == ["client.properties"]
    assert (world.pc_dir / a / "client.properties").read_text(encoding="utf-8") == orig
    assert r[b]["result"] == "success"
    rows = {x["pc_id"]: x["result"] for x in inquiry.result_table(world, board.get_post(world, pid), parse_pc_field(f"{a}, {b}"))}
    assert rows == {a: "실패 후 원복", b: "조치함"}


def test_note_refused_while_fix_pending(base, world):
    pid = add(world, base["hidden"]["scenarios"]["op_config"][0])
    T.update_client_config(world, pid)
    assert T.post_operator_note(world, pid, "회신")["is_error"]
    T.restart_client_service(world, pid)
    r = T.post_operator_note(world, pid, "회신")
    assert r["visibility"] == "operator" and r["reply_type"] == "결과 회신"


def test_tools_cannot_reach_hidden_file(world):
    pid = add(world, "PC-A0101")
    for name, args in [("get_client_state", {"pc_id": "../../기록/정답_PC원인"}),
                       ("get_server_log", {"pc_id": "PC-A0101/../../.."}),
                       ("get_post", {"post_id": pid, "path": "기록/정답_PC원인.json"}),
                       ("read_file", {"path": "기록/정답_PC원인.json"})]:
        text, is_err, _ = T.execute(world, name, args)
        assert is_err and "restart_succeeds_if" not in text
    src = Path(T.__file__).read_text(encoding="utf-8") + Path(inquiry.__file__).read_text(encoding="utf-8")
    assert "hidden_path" not in src and "HIDDEN_CAUSES" not in src



def _ideal_evidence(world, hidden, pcs):
    """시험용: 메시지 앞부분으로 소스를 찾아 ErrorKind가 있는 첫 줄을 근거로 쓴다(모델이 할 일을 흉내 낸다)."""
    ev = []
    for pc in pcs:
        q = hidden[pc]["error_message"].split(":")[0].split("(")[0].strip()
        hits = [h for h in T.find_error_in_source(world, q)["hits"] if "error_kind" in h]
        if hits:
            ev.append({"pc_id": pc, "location": hits[0]["location"]})
    return ev


def test_source_evidence_reaches_ideal_answers(base, world):
    """개선 단계 문의(#21~27): 소스 근거를 주면 표기 변형은 조치함, 신규 원인은 조건 미충족(정답)과 같아야 한다.
    #27(프록시를 거치는 PC의 표현만 다른 접속 오류)은 2차 개선, #28(넓은 리터럴에 감싸인 새로운 원인)은 3차 개선부터 맞는다.
    #29(키가 메시지에 없는 설정 오류)는 근거가 확인돼도 고칠 키를 정할 수 없어 조건 미충족이다."""
    hidden = base["hidden"]["pcs"]
    for p in base["posts"][20:29]:
        pid = p["post_id"]
        board.create_post(world, p["author"], p["title"], p["pc_field"], p["body"], p["created_at"], post_id=pid)
        parsed = parse_pc_field(p["pc_field"], p["body"])
        ev = _ideal_evidence(world, hidden, parsed["targets"])
        d = T.diagnose_targets(world, pid, error_evidence=ev)
        for name in d["write_tools_needed_in_order"]:
            assert not T.execute(world, name, {"post_id": pid, "error_evidence": ev})[1]
        got = {r["pc_id"]: r["result"] for r in inquiry.result_table(world, board.get_post(world, pid), parsed)}
        assert got == base["answers"][str(pid)]["expected"], pid
    new = set(base["hidden"]["scenarios"]["op_new_cause"])
    assert not {h["pc_id"] for h in history.rows(world)} & new


def test_source_evidence_is_checked_by_code(base, world):
    """모델이 준 근거는 코드가 확인한다: 다른 줄, 신규 원인 줄, 소스 밖 경로, 대상이 아닌 PC의 근거는 통하지 않는다."""
    sc, hidden = base["hidden"]["scenarios"], base["hidden"]["pcs"]
    conn = next(pc for pc in sc["op_variant"] if hidden[pc]["error_message"].startswith("Connection refused"))
    new = next(pc for pc in sc["op_new_cause"] if hidden[pc]["error_message"].startswith("connection reset"))
    pid = add(world, f"{conn}, {new}")
    good = _ideal_evidence(world, hidden, [conn])[0]["location"]
    reset_line = _ideal_evidence(world, hidden, [new])[0]["location"]
    cases = {
        "com/abz/client/HeartbeatSender.java:34": False,      # 다른 메시지의 줄
        "com/abz/client/ErrorKind.java:14": False,            # 대응 정의 줄(리터럴과 메시지가 맞지 않음)
        "../../기록/정답_PC원인.json:1": False,                 # 소스 밖
        "com/abz/client/ServerConnection.java:9999": False,
        "아무거나": False,
        good: True,
    }
    for loc, ok in cases.items():
        plan = {x["pc_id"]: x for x in T.diagnose_targets(world, pid, error_evidence=[{"pc_id": conn, "location": loc}])["plans"]}
        assert plan[conn]["eligible"] is ok, loc
    # 신규 원인 PC는 자기 메시지의 줄을 근거로 줘도, 표기 변형 PC의 줄을 줘도 조치하지 않는다.
    for loc in (reset_line, good):
        plan = {x["pc_id"]: x for x in T.diagnose_targets(world, pid, error_evidence=[{"pc_id": new, "location": loc}])["plans"]}
        assert not plan[new]["eligible"], loc
    # 대상이 아닌 PC의 근거는 무시한다.
    d = T.diagnose_targets(world, pid, error_evidence=[{"pc_id": "PC-A0101", "location": good}])
    assert d["error_evidence_ignored"] == ["PC-A0101"]
    r = {x["pc_id"]: x for x in T.restart_client_service(world, pid, error_evidence=[{"pc_id": conn, "location": good},
                                                                                     {"pc_id": new, "location": reset_line}])["results"]}
    assert r[conn]["result"] == "success" and "skipped" in r[new]
    assert T.execute(world, "find_error_in_source", {"query": "connection"})[0].count("location") <= 10


def test_second_supplement_world_and_answers(base):
    """2차 보강(#27~29): 숨은 원인, PC 폴더, 정답이 서로 맞는지 확인한다(조치 코드와 독립)."""
    from abzlib import source
    from abzlib.patterns import match_pattern
    w = World(base["root"])
    ctx = inquiry.Context(w)
    sc, hidden, ans = base["hidden"]["scenarios"], base["hidden"]["pcs"], base["answers"]
    a, b, c = sc["op_variant_proxy"][0], sc["op_new_cause_wrapped"][0], sc["op_variant_config_nokey"][0]
    for pc in (a, b, c):
        assert ctx.status(pc)["status"] == "stopped" and vpc.last_error(w, pc) == hidden[pc]["error_message"], pc
        assert match_pattern(hidden[pc]["error_message"], ctx.patterns) is None, pc     # 패턴표와 정확히 일치하지 않는다
    # (a) 프록시를 거치는 PC. 동료 PC의 합의(ADR 0004)로 정답 프록시 값이 나오고, 현재 값은 틀려 있다.
    assert ctx.master[a]["internal_ip"] and hidden[a]["restart_succeeds_if"] == "jvm_proxy_correct"
    px = inquiry.compute_proxy(ctx, a)
    assert px["value"] == hidden[a]["expected_proxy"] and px["peers_running"] >= 3
    assert vpc.jvm_proxy(vpc.read_jvm(w, a)) != hidden[a]["expected_proxy"]
    # 같은 사내망 IP 그룹의 다른 정지 PC와 30분 안에 멈추지 않았다(동시 장애로 보이지 않는다).
    t0 = datetime.strptime(hidden[a]["stopped_at"], vpc.TS)
    for p, r in ctx.master.items():
        if p != a and r["ip"] == ctx.master[a]["ip"] and r["internal_ip"] and ctx.status(p)["status"] == "stopped":
            assert abs(datetime.strptime(ctx.status(p)["since"], vpc.TS) - t0).total_seconds() > 30 * 60, p
    # (b), (c) 직접 접속 PC. 재시작해도 다시 실패한다.
    for pc in (b, c):
        assert not ctx.master[pc]["internal_ip"] and hidden[pc]["restart_succeeds_if"] == "never", pc
    assert hidden[b]["cause"] == "new_cause" and hidden[c]["cause"] == "variant_config_nokey"
    # 메시지는 모두 소스의 리터럴로 들어 있다. (b)의 넓은 리터럴 안 값은 패턴표 대응이 없는 오류 종류의 리터럴과 맞는다.
    lits = [(x, line) for f in w.source_dir.rglob("*.java") for line in f.read_text(encoding="utf-8").splitlines()
            if "ErrorKind." in line for x in source.LITERAL.findall(line)]
    for pc in (a, b, c):
        assert any(re.fullmatch(source.literal_regex(x), hidden[pc]["error_message"]) for x, _ in lits), pc
    assert any("LICENSE_EXPIRED" in line and re.fullmatch(source.literal_regex(x), "license expired on 2026-11-20")
               for x, line in lits)
    # 정답
    assert {str(n): ans[str(n)]["expected"] for n in (27, 28, 29)} == {
        "27": {a: inquiry.DONE}, "28": {b: inquiry.NOT_MET}, "29": {c: inquiry.NOT_MET}}
    assert [ans[str(n)]["reply_type"] for n in (27, 28, 29)] == ["결과 회신", "방법 회신", "방법 회신"]


def test_wrapped_new_cause_evidence_rejected(base, world, monkeypatch):
    """3차 개선(문제 B): 넓은 리터럴 'failed to send heartbeat: %s'는 %s 자리에 새로운 원인이 들어간 메시지도 맞춘다.
    1차 개선 코드(%s 자리 값 확인 없음)에서는 #28의 근거가 통과해 재시작 대상이 되고(정답은 조건 미충족),
    3차 개선에서는 %s 자리 값이 ErrorKind.LICENSE_EXPIRED(대응 없음)의 리터럴과 맞아 근거를 거부한다."""
    from abzlib import source
    b = base["hidden"]["scenarios"]["op_new_cause_wrapped"][0]
    pid = add(world, b)
    loc = next(h["location"] for h in T.find_error_in_source(world, "failed to send heartbeat")["hits"] if "error_kind" in h)
    ev = [{"pc_id": b, "location": loc}]
    with monkeypatch.context() as m:                     # 1차 개선 코드 재현: %s 자리 값을 보지 않는다
        m.setattr(source, "wrapped_other_cause", lambda *a, **k: None)
        plan = T.diagnose_targets(world, pid, error_evidence=ev)["plans"][0]
        assert plan["source_evidence"]["ok"] and plan["eligible"]
    plan = T.diagnose_targets(world, pid, error_evidence=ev)["plans"][0]
    se = plan["source_evidence"]
    assert not se["ok"] and not plan["eligible"] and plan["category"] == inquiry.NOT_MET
    assert "LICENSE_EXPIRED" in se["reason"] and "새로운 원인" in se["reason"] and "license expired on" in se["reason"]
    r = T.restart_client_service(world, pid, error_evidence=ev)
    assert "skipped" in r["results"][0] and not history.rows(world, pid)


def test_wrapped_check_keeps_same_kind_values(base, world):
    """3차 개선의 회귀 확인: 같은 ErrorKind의 값('read timed out')이 든 넓은 리터럴 메시지와 표현만 다른 메시지는 그대로 통과한다."""
    from abzlib import source
    from abzlib.patterns import load_patterns
    pats = load_patterns(world.pattern_table)
    hb = next(h["location"] for h in T.find_error_in_source(world, "failed to send heartbeat")["hits"] if "error_kind" in h)
    assert source.verify_evidence(world, hb, "failed to send heartbeat: read timed out", pats)["ok"]
    assert not source.verify_evidence(world, hb, "failed to send heartbeat: connection refused: 10.10.0.5:8443", pats)["ok"]
    cfg = next(h["location"] for h in T.find_error_in_source(world, "failed to parse config")["hits"] if "error_kind" in h)
    assert source.verify_evidence(world, cfg, "failed to parse config: retry_count=abc", pats)["ok"]


def test_proxy_variant_gets_proxy_judgement(base, world):
    """2차 개선(문제 A): 프록시를 거치는 PC의 표현만 다른 접속 오류도 근거가 확인되면 표준 메시지와 같은 프록시 판단을 받는다.
    근거가 없으면 지금처럼 조건 미충족이다."""
    a = base["hidden"]["scenarios"]["op_variant_proxy"][0]
    pid = add(world, a)
    assert not T.diagnose_targets(world, pid)["plans"][0]["eligible"]
    loc = _ideal_evidence(world, base["hidden"]["pcs"], [a])[0]["location"]
    d = T.diagnose_targets(world, pid, error_evidence=[{"pc_id": a, "location": loc}])
    plan = d["plans"][0]
    assert plan["source_evidence"]["ok"] and plan["eligible"]
    assert plan["action"]["type"] == "jvm_proxy" and plan["action"]["value"] == base["hidden"]["pcs"][a]["expected_proxy"]
    assert d["write_tools_needed_in_order"] == ["update_jvm_proxy", "restart_client_service"]


def _stop_peer(world, pc, since, message):
    vpc.write_service(world, pc, "stopped", since)
    vpc.append_log(world, pc, [(datetime.strptime(since, vpc.TS), "ERROR", message)])


def test_simultaneous_check_counts_variant_peers(base, world):
    """2차 개선(문제 A): 동료 PC가 표현만 다른 접속 오류로 30분 안에 멈췄으면, 그 PC에 근거가 없어도 코드가 소스 리터럴과
    ErrorKind 대응을 직접 확인해 동시 장애로 센다. 접속 오류가 아닌 메시지(연결 끊김 등)는 세지 않는다."""
    a = base["hidden"]["scenarios"]["op_variant_proxy"][0]
    ctx = inquiry.Context(world)
    peer = next(p for p, r in sorted(ctx.master.items()) if p != a and r["ip"] == ctx.master[a]["ip"] and r["internal_ip"]
                and ctx.status(p)["status"] == "running")
    near = (datetime.strptime(ctx.status(a)["since"], vpc.TS).replace(second=0)).strftime(vpc.TS)
    pid = add(world, a)
    loc = _ideal_evidence(world, base["hidden"]["pcs"], [a])[0]["location"]
    ev = [{"pc_id": a, "location": loc}]
    _stop_peer(world, peer, near, "connection reset by peer")             # 새로운 원인: 세지 않는다
    assert T.diagnose_targets(world, pid, error_evidence=ev)["plans"][0]["eligible"]
    _stop_peer(world, peer, near, "Connection refused: connect")          # 표현만 다른 접속 오류: 센다
    plan = T.diagnose_targets(world, pid, error_evidence=ev)["plans"][0]
    assert not plan["eligible"] and plan["together"] == sorted([a, peer]) and "장애 의심" in plan["reason"]
    r = T.update_jvm_proxy(world, pid, error_evidence=ev)
    assert r["applied"] == 0 and not history.rows(world, pid)
    # 코드 판정 함수 자체
    assert inquiry.is_conn_refused(ctx, "Connection refused: connect")
    assert inquiry.is_conn_refused(ctx, "connection refused: 10.10.0.5:8443")
    assert not inquiry.is_conn_refused(ctx, "connection reset by peer")
    assert not inquiry.is_conn_refused(ctx, "Connection refused: something else")


def resp(*blocks, stop="tool_use"):
    return {"stop_reason": stop, "content": list(blocks), "usage": None, "model": "fake"}


def use(i, name, **inp):
    return {"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}


class Fake:
    def __init__(self, script):
        self.script = script
        self.calls = 0

    def create(self, **kw):
        assert kw["tools"] and kw["system"] and "tool_choice" not in kw
        self.calls += 1
        return self.script(self.calls)


def test_agent_loop_happy_path(base, world, tmp_path):
    pc = base["hidden"]["scenarios"]["op_proxy_wrong"][0]
    pid = add(world, pc)
    steps = [[use(1, "diagnose_targets", post_id=pid)], [use(2, "update_jvm_proxy", post_id=pid)],
             [use(3, "restart_client_service", post_id=pid)], [use(4, "post_operator_note", post_id=pid, message="프록시 값을 고쳤다.")]]
    r = inquiry_agent.run(pid, world, client=Fake(lambda n: resp(*steps[n - 1])), runs_dir=tmp_path / "runs")
    assert r["status"] == "done" and r["model_calls"] == 4
    note = board.list_notes(world, pid)
    assert len(note) == 1 and note[0]["body"].startswith("[결과 회신 초안]") and f"{pc} | 조치함" in note[0]["body"]


def test_agent_loop_model_call_limit(world, tmp_path):
    pid = add(world, "PC-A0101")
    fake = Fake(lambda n: resp(use(n, "get_error_patterns")))
    r = inquiry_agent.run(pid, world, client=fake, runs_dir=tmp_path / "runs")
    assert r["status"] == "aborted" and fake.calls == 15 and "모델 호출 상한" in r["abort_reason"]
    assert board.list_notes(world, pid)[0]["body"].startswith("[처리 중단")


def test_agent_loop_tool_call_limit(world, tmp_path):
    pid = add(world, "PC-A0101")
    fake = Fake(lambda n: resp(*[use(f"{n}_{k}", "get_pc_info", pc_id="PC-A0101") for k in range(6)]))
    r = inquiry_agent.run(pid, world, client=fake, runs_dir=tmp_path / "runs")
    assert r["status"] == "aborted" and r["tool_calls"] == 40 and "Tool 호출 상한" in r["abort_reason"]


def test_agent_loop_refusal_and_no_note(world, tmp_path):
    pid = add(world, "PC-A0101")
    r = inquiry_agent.run(pid, world, client=Fake(lambda n: resp(stop="refusal")), runs_dir=tmp_path / "runs")
    assert r["status"] == "aborted" and "거절" in r["abort_reason"]
    pid2 = add(world, "PC-A0102")
    r = inquiry_agent.run(pid2, world, client=Fake(lambda n: resp({"type": "text", "text": "끝"}, stop="end_turn")),
                          runs_dir=tmp_path / "runs")
    assert r["status"] == "aborted" and board.list_notes(world, pid2)
