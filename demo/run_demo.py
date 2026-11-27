"""발표 시연용 실행기. 저장소 원본을 바꾸지 않고 시연/ 폴더에서 돌린다.

사용:
  python -m demo.run_demo report --date 2026-11-25        # 아이템 ①: 일일 리포트를 만들고 HTML을 연다
  python -m demo.run_demo inquiry --posts 3 17 27         # 아이템 ②: 가상 세계 복사본에서 문의를 처리한다
  python -m demo.run_demo inquiry --posts 3 --no-open     # 브라우저를 열지 않는다

ANTHROPIC_API_KEY 환경변수가 필요하다. 아이템 ②는 매번 새 복사본을 만들므로 같은 문의를 여러 번 시연할 수 있다.
"""
import argparse
import difflib
import json
import os
import shutil
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from abzlib import paths

OUT = paths.ROOT / "시연"
PC_FILES = ("service.json", "client.properties", "jvm.args")


def line(title=""):
    print("\n" + ("=" * 8 + f" {title} " if title else "") + "=" * max(8, 70 - len(title)))


def open_file(p, do_open):
    print(f"파일: {p}")
    if do_open:
        webbrowser.open(Path(p).resolve().as_uri())


def demo_report(day, do_open):
    from runner.daily_report import run
    out = OUT / f"리포트_{datetime.now():%H%M%S}"
    r = run(day, publish=True, storage_dir=out / "storage", outbox=out / "outbox.jsonl", runs_dir=out / "runs")
    if r.get("is_error"):
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return
    s, rec = r["summary"], r["record"]
    t = s["totals"]
    line(f"일일 리포트 {day}")
    print(f"등록 PC {t['registered']}대 · 정상 PC 비율 {t['normal_ratio']}% · 이상 PC {t['anomalous']}대 · 식별 실패 {len(s['identify_fail'])}건")
    line("LLM 판단 1: 처음 보는 오류 메시지 분류")
    for m in rec["message_classes"] or [{"message": "(패턴표와 다른 메시지 없음)", "matched_pattern": "", "reason": ""}]:
        print(f"- {m['message']}  →  {m['matched_pattern']}  ({m['reason']})")
    line("LLM 판단 2: 공통 원인 후보 판정")
    for c in rec["clusters"]:
        mark = "공통 원인" if c["meaningful"] else "공통 원인 아님"
        px = f" · 프록시 PC 지목: {c['proxy_pc']}" if c["proxy_pc"] else ""
        print(f"- [{mark}] 신호 {c['signal']} · {len(c['pcs'])}대 · 공유 {c['shared']}{px}\n    {c['cause']}")
    line("LLM 판단 3: 요약")
    print(rec["llm_output"]["summary"])
    g = rec.get("summary_guard") or {}
    print(f"\n[발행 전 검사] 첫 요약의 출처 없는 숫자: {g.get('first_ungrounded') or '없음'} · 재생성: {g.get('regenerated')} · 뺀 문장: {len(g.get('dropped_sentences', []))}")
    print(f"모델 {rec['usage'].get('model')} · 입력 {rec['usage'].get('input_tokens')} 토큰 · 출력 {rec['usage'].get('output_tokens')} 토큰")
    open_file(out / "storage" / "reports" / f"{day}.html", do_open)


def snapshot(world, pcs):
    snap = {}
    for pc in pcs:
        d = world.pc_dir / pc
        snap[pc] = {f: (d / f).read_text(encoding="utf-8") if (d / f).exists() else "" for f in PC_FILES}
    return snap


def short(v, n=160):
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "…"


def demo_inquiry(post_ids, do_open):
    from eval.score_inquiries import make_world
    from runner.inquiry_agent import run
    from tools import board
    from abzlib.targets import parse_pc_field
    data = json.loads(paths.INQUIRY_SET.read_text(encoding="utf-8"))
    posts = {p["post_id"]: p for p in data["posts"]}
    out = OUT / f"문의_{datetime.now():%H%M%S}"
    world = make_world(paths.ROOT, out)
    for pid in post_ids:
        p = posts.get(pid)
        if p is None:
            print(f"문의 #{pid}는 시험 세트에 없다(1~{max(posts)}).")
            continue
        board.create_post(world, p["author"], p["title"], p["pc_field"], p["body"], p["created_at"], post_id=pid)
        try:
            parsed = parse_pc_field(p["pc_field"], p["body"])
            pcs = [x for x in parsed["targets"] + parsed["body_only"] if (world.pc_dir / x).exists()]
        except Exception:
            pcs = []
        before = snapshot(world, pcs)
        line(f"문의 #{pid} · {p['title']}")
        print(f"설비 PC ID: {p['pc_field']}\n본문: {p['body']}")
        r = run(pid, world, runs_dir=out / "runs")
        rec = json.loads(Path(r["record_path"]).read_text(encoding="utf-8"))
        line("에이전트가 고른 Tool")
        for i, tc in enumerate(rec["tool_calls"], 1):
            print(f"{i}. {tc['name']}({short(tc['input'])}){'  [거부/오류]' if tc['is_error'] else ''}")
        after = snapshot(world, pcs)
        line("PC에 생긴 변화")
        changed = False
        for pc in pcs:
            for f in PC_FILES:
                if before[pc][f] != after[pc][f]:
                    changed = True
                    diff = [l for l in difflib.unified_diff(before[pc][f].splitlines(), after[pc][f].splitlines(), lineterm="", n=0)
                            if l[:1] in "+-" and not l.startswith(("+++", "---"))]
                    print(f"{pc}/{f}: " + " | ".join(diff[:6]))
        if not changed:
            print("바뀐 PC 파일 없음(조치하지 않음)")
        line("운영자 전용 메모(회신 초안)")
        for n in board.list_notes(world, pid):
            print(n["body"])
        print(f"\n상태 {r['status']} · 모델 호출 {r['model_calls']}회 · Tool 호출 {r['tool_calls']}회")
    html_dir = board.render_html(world, out / "board")
    first = next((pid for pid in post_ids if pid in posts), None)
    open_file(Path(html_dir) / "operator" / (f"post_{first}.html" if first else "index.html"), do_open)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="아이템 ①, ② 시연")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("report", help="아이템 ① 일일 리포트")
    a.add_argument("--date", default="2026-11-25")
    a.add_argument("--no-open", action="store_true")
    b = sub.add_parser("inquiry", help="아이템 ② 문의 처리")
    b.add_argument("--posts", type=int, nargs="+", default=[3, 17, 27])
    b.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    if args.cmd == "report":
        demo_report(args.date, not args.no_open)
    else:
        demo_inquiry(args.posts, not args.no_open)


if __name__ == "__main__":
    main()
