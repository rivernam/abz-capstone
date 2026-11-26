"""아이템 ② 채점(구현명세 §7 ②): 문의 20건.

문의마다 저장소의 재료/와 숨은 원인 파일을 임시 폴더에 복사한 새 세계에서 처리한다. 저장소의 재료/는 바뀌지 않는다.

지표
- PC별 결과 일치율: 회신 표(조치 이력 기준)의 PC별 결과 6종이 정답과 같은 비율.
- 잘못된 PC 대상 실행 건수: 정답상 조치하지 않아야 하는 PC에 쓰기 Tool이 실행됐거나 PC 폴더가 바뀐 건수(0이어야 함).
- 회신 유형 일치율: 결과 회신/방법 회신.
- 운영자 메모 외 쓰기 건수: 게시글 변경, operator가 아닌 메모(0이어야 함).
- 재시작 후 복구 성공률: 재시작한 PC 중 검증을 통과한 비율.
- 처리 중단 건수, 모델 호출과 Tool 호출 수, 토큰.

사용:
  python -m eval.score_inquiries [--runs 1] [--only 1,3] [--workers 4]
  python -m eval.score_inquiries --scripted   # LLM 없이 진단 결과대로 Tool을 부르는 기준선
결과: 기록/평가/inquiries_<시각>.json
"""
import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from abzlib import history, inquiry, paths
from abzlib.targets import parse_pc_field
from abzlib.world import World
from tools import board
from tools import inquiry_tools as T

WRITE_PC_TOOLS = ("update_client_config", "update_jvm_proxy", "restart_client_service")


def make_world(src_root, dst_root):
    """원본 세계의 재료/와 숨은 원인 파일을 복사해 독립된 세계를 만든다."""
    src, dst = Path(src_root), Path(dst_root)
    shutil.copytree(src / "재료", dst / "재료")
    (dst / "기록").mkdir(parents=True, exist_ok=True)
    shutil.copy2(src / "기록" / "정답_PC원인.json", dst / "기록" / "정답_PC원인.json")
    return World(dst)


def pc_digests(world):
    out = {}
    for d in sorted(world.pc_dir.iterdir()):
        h = hashlib.sha256()
        for f in sorted(d.rglob("*")):
            if f.is_file():
                h.update(str(f.relative_to(d)).encode() + f.read_bytes())
        out[d.name] = h.hexdigest()
    return out


def scripted_agent(world, post_id):
    """LLM 없는 기준선: 진단 결과의 쓰기 Tool 순서를 그대로 부르고 고정 문장으로 회신한다."""
    diag = T.diagnose_targets(world, post_id)
    for name in diag.get("write_tools_needed_in_order", []):
        T.execute(world, name, {"post_id": post_id})
    T.post_operator_note(world, post_id, "문의하신 PC를 확인했다. PC별 결과는 아래 표와 같다.")
    return {"status": "done", "model_calls": 0, "tool_calls": 0}


def score_one(post, ans, world, agent):
    pid = post["post_id"]
    board.create_post(world, post["author"], post["title"], post["pc_field"], post["body"], post["created_at"], post_id=pid)
    before_posts = board.list_posts(world)
    before = pc_digests(world)
    run = agent(world, pid)
    after = pc_digests(world)
    parsed = parse_pc_field(post["pc_field"], post["body"])
    table = {r["pc_id"]: r["result"] for r in inquiry.result_table(world, board.get_post(world, pid), parsed)}
    exp = ans["expected"]
    acted = set(ans["acted_pcs"])
    hist = history.rows(world, pid)
    wrong = {h["pc_id"] for h in hist if h["tool"] in WRITE_PC_TOOLS and h["pc_id"] not in acted}
    wrong |= {pc for pc in after if after[pc] != before.get(pc) and pc not in acted}
    notes = board.list_notes(world, pid)
    con = sqlite3.connect(world.board_db)
    other_notes = con.execute("SELECT COUNT(*) FROM notes WHERE visibility != 'operator'").fetchone()[0]
    con.close()
    non_operator_writes = other_notes + (board.list_posts(world) != before_posts)
    head = notes[0]["body"].splitlines()[0] if notes else ""
    got_type = "결과 회신" if "결과 회신" in head else "방법 회신" if "방법 회신" in head else None
    restarts = [h for h in hist if h["tool"] == "restart_client_service"]
    mism = {pc: {"expected": e, "got": table.get(pc)} for pc, e in exp.items() if table.get(pc) != e}
    return {
        "post_id": pid, "category": ans["category"], "status": run.get("status"), "abort_reason": run.get("abort_reason"),
        "pc_total": len(exp), "pc_correct": len(exp) - len(mism), "mismatches": mism,
        "wrong_target_executions": sorted(wrong), "reply_type_expected": ans["reply_type"], "reply_type_got": got_type,
        "non_operator_writes": non_operator_writes, "notes": len(notes),
        "restarts": len(restarts), "restart_success": sum(h["result"] == "success" for h in restarts),
        "model_calls": run.get("model_calls"), "tool_calls": run.get("tool_calls"), "record_path": run.get("record_path"),
        "note": notes[0]["body"] if notes else None,
    }


def summarize(rows):
    n = len(rows)
    pcs = sum(r["pc_total"] for r in rows)
    rs = sum(r["restarts"] for r in rows)
    return {
        "inquiries": n,
        "pc_result_accuracy": round(sum(r["pc_correct"] for r in rows) / pcs, 3) if pcs else None,
        "inquiries_all_pcs_correct": sum(r["pc_correct"] == r["pc_total"] for r in rows),
        "wrong_target_executions": sum(len(r["wrong_target_executions"]) for r in rows),
        "reply_type_accuracy": round(sum(r["reply_type_got"] == r["reply_type_expected"] for r in rows) / n, 3) if n else None,
        "non_operator_writes": sum(r["non_operator_writes"] for r in rows),
        "restart_recovery_rate": round(sum(r["restart_success"] for r in rows) / rs, 3) if rs else None,
        "restarts": rs,
        "aborted": sum(r["status"] != "done" for r in rows),
        "avg_model_calls": round(sum(r["model_calls"] or 0 for r in rows) / n, 2) if n else None,
        "avg_tool_calls": round(sum(r["tool_calls"] or 0 for r in rows) / n, 2) if n else None,
    }


def usage_of(record_path):
    if not record_path:
        return {}
    rec = json.loads(Path(record_path).read_text(encoding="utf-8"))
    tot = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    for c in rec["calls"]:
        for k in tot:
            tot[k] += (c.get("usage") or {}).get(k) or 0
    return tot


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="아이템 ② 문의 20건 채점")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--only", help="문의 번호 쉼표 목록")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--scripted", action="store_true", help="LLM 없이 진단 결과대로 실행하는 기준선")
    a = ap.parse_args()
    posts = json.loads(paths.INQUIRY_SET.read_text(encoding="utf-8"))["posts"]
    answers = json.loads(paths.INQUIRY_ANSWER.read_text(encoding="utf-8"))["answers"]
    if a.only:
        keep = {int(x) for x in a.only.split(",")}
        posts = [p for p in posts if p["post_id"] in keep]
    runs_dir = paths.EVAL_DIR / "runs"
    if a.scripted:
        agent = scripted_agent
    else:
        from runner.inquiry_agent import run as run_agent
        agent = lambda world, pid: run_agent(pid, world, runs_dir=runs_dir)

    def job(post):
        with tempfile.TemporaryDirectory(prefix="abz_inq_") as tmp:
            world = make_world(paths.ROOT, tmp)
            r = score_one(post, answers[str(post["post_id"])], world, agent)
            r["usage"] = usage_of(r["record_path"])
            return r

    all_runs = []
    for i in range(a.runs):
        with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
            rows = list(ex.map(job, posts))
        s = summarize(rows)
        s["tokens"] = {k: sum(r["usage"].get(k, 0) for r in rows) for k in ("input_tokens", "output_tokens",
                                                                              "cache_read_input_tokens", "cache_creation_input_tokens")}
        all_runs.append({"run": i + 1, "summary": s, "inquiries": rows})
        print(json.dumps({"run": i + 1, **s}, ensure_ascii=False))
    keys = [k for k, v in all_runs[0]["summary"].items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    mean = {k: round(sum(r["summary"][k] or 0 for r in all_runs) / len(all_runs), 3) for k in keys}
    out = {"mode": "scripted" if a.scripted else "llm", "model": None if a.scripted else __import__("os").environ.get(
        "ABZ_INQUIRY_MODEL", "claude-sonnet-5-5"), "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mean": mean, "runs": all_runs}
    paths.EVAL_DIR.mkdir(parents=True, exist_ok=True)
    path = paths.EVAL_DIR / f"inquiries_{'scripted_' if a.scripted else ''}{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"mean": mean, "result": str(path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
