"""아이템 ② 시험: 대상 파서, 게시판, 프록시 계산, 쓰기 Tool 안전 검사, 가상 재시작, 에이전트 루프 상한.

저장소의 재료/는 쓰지 않는다. tmp 경로에 generate()로 세계를 만들고, 쓰는 시험은 그 복사본을 쓴다.
"""
import json
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
from generator import make_inquiries
from generator.make_world import generate
from runner import inquiry_agent
from tools import board
from tools import inquiry_tools as T

CLOCK = lambda: datetime(2026, 11, 26, 9, 0, 0)


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    root = tmp_path_factory.mktemp("base")
    generate(root)
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
