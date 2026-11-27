"""발표 시연용 웹 화면. 표준 라이브러리만 쓴다.

사용: python -m demo.web [--port 8765]  →  http://127.0.0.1:8765
- 일일 리포트: 날짜를 골라 리포트를 만들고 LLM 판단과 발행된 HTML을 본다.
- 문의 자동 조치: 시험 세트의 문의를 골라 에이전트가 Tool을 고르는 과정을 실시간으로 본다.
  문의마다 가상 세계 복사본(시연/web_*)을 새로 만들므로 저장소 원본은 바뀌지 않는다.
ANTHROPIC_API_KEY 환경변수가 필요하다. 127.0.0.1에서만 연다.
"""
import argparse
import difflib
import json
import mimetypes
import sys
import threading
import traceback
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from abzlib import paths

OUT = paths.ROOT / "시연"
STATIC = Path(__file__).resolve().parent / "static"
PC_FILES = ("service.json", "client.properties", "jvm.args")
JOBS = {}
LOCK = threading.Lock()


def new_job(kind):
    jid = uuid.uuid4().hex[:10]
    with LOCK:
        JOBS[jid] = {"id": jid, "kind": kind, "status": "running", "events": [], "result": None, "error": None,
                     "started": datetime.now().strftime("%H:%M:%S")}
    return jid


def push(jid, ev):
    with LOCK:
        JOBS[jid]["events"].append(ev)


def finish(jid, result=None, error=None):
    with LOCK:
        JOBS[jid].update(status="error" if error else "done", result=result, error=error)


def report_job(jid, day):
    from runner.daily_report import run
    out = OUT / f"web_report_{jid}"
    push(jid, {"type": "stage", "text": "서버 로그를 집계하고 LLM에 판단을 요청하는 중"})
    r = run(day, publish=True, storage_dir=out / "storage", outbox=out / "outbox.jsonl", runs_dir=out / "runs")
    if r.get("is_error"):
        return finish(jid, error=r.get("error"))
    s, rec = r["summary"], r["record"]
    t = s["totals"]
    finish(jid, {
        "date": day,
        "totals": {"registered": t["registered"], "normal_ratio": t["normal_ratio"], "anomalous": t["anomalous"],
                   "identify_fail": len(s["identify_fail"]),
                   "prev_anomalous": (s["vs_prev_day"] or {}).get("anomalous", {}).get("prev")},
        "message_classes": rec["message_classes"],
        "clusters": rec["clusters"],
        "summary": rec["llm_output"]["summary"],
        "guard": rec.get("summary_guard"),
        "usage": rec["usage"],
        "report_url": f"/files/web_report_{jid}/storage/reports/{day}.html",
    })


def snapshot(world, pcs):
    return {pc: {f: ((world.pc_dir / pc / f).read_text(encoding="utf-8") if (world.pc_dir / pc / f).exists() else "")
                 for f in PC_FILES} for pc in pcs}


def inquiry_job(jid, pid):
    from abzlib.targets import parse_pc_field
    from eval.score_inquiries import make_world
    from runner.inquiry_agent import run
    from tools import board
    posts = {p["post_id"]: p for p in json.loads(paths.INQUIRY_SET.read_text(encoding="utf-8"))["posts"]}
    p = posts[pid]
    out = OUT / f"web_inquiry_{jid}"
    world = make_world(paths.ROOT, out)
    board.create_post(world, p["author"], p["title"], p["pc_field"], p["body"], p["created_at"], post_id=pid)
    parsed = parse_pc_field(p["pc_field"], p["body"])
    pcs = [x for x in parsed["targets"] + parsed["body_only"] if (world.pc_dir / x).exists()]
    before = snapshot(world, pcs)
    r = run(pid, world, runs_dir=out / "runs", on_event=lambda e: push(jid, e))
    after = snapshot(world, pcs)
    changes = []
    for pc in pcs:
        for f in PC_FILES:
            if before[pc][f] != after[pc][f]:
                d = [l for l in difflib.unified_diff(before[pc][f].splitlines(), after[pc][f].splitlines(), lineterm="", n=0)
                     if l[:1] in "+-" and not l.startswith(("+++", "---"))]
                changes.append({"pc": pc, "file": f, "lines": d})
    status = {pc: json.loads(after[pc]["service.json"] or "{}").get("status") for pc in pcs}
    notes = [n["body"] for n in board.list_notes(world, pid)]
    finish(jid, {"post_id": pid, "status": r["status"], "model_calls": r["model_calls"], "tool_calls": r["tool_calls"],
                 "changes": changes, "pc_status": status, "notes": notes, "body_only": parsed["body_only"],
                 "over_limit": parsed["over_limit"]})


def start(target, *args):
    jid = new_job(target.__name__)

    def wrap():
        try:
            target(jid, *args)
        except Exception as e:  # 화면에 사유를 보여 준다
            traceback.print_exc()
            finish(jid, error=f"{type(e).__name__}: {e}")
    threading.Thread(target=wrap, daemon=True).start()
    return jid


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        from urllib.parse import unquote, urlparse
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html"):
            return self.send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/meta":
            ans = json.loads((paths.RECORD_DIR / "정답표_일일리포트.json").read_text(encoding="utf-8"))
            posts = json.loads(paths.INQUIRY_SET.read_text(encoding="utf-8"))["posts"]
            return self.send(200, {"report_dates": ans["report_dates"],
                                   "posts": [{k: p[k] for k in ("post_id", "category", "author", "title", "pc_field", "body", "created_at")}
                                             for p in posts]})
        if path.startswith("/api/job/"):
            job = JOBS.get(path.rsplit("/", 1)[-1])
            return self.send(200, job) if job else self.send(404, {"error": "없는 작업이다."})
        if path.startswith("/files/"):
            f = (OUT / path[len("/files/"):]).resolve()
            if OUT.resolve() not in f.parents or not f.is_file():
                return self.send(404, {"error": "파일이 없다."})
            return self.send(200, f.read_bytes(), (mimetypes.guess_type(f.name)[0] or "text/plain") + "; charset=utf-8")
        return self.send(404, {"error": "없는 경로다."})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self.send(400, {"error": "JSON 본문이 아니다."})
        if self.path == "/api/report":
            return self.send(200, {"job": start(report_job, str(body.get("date", "2026-11-25")))})
        if self.path == "/api/inquiry":
            try:
                pid = int(body.get("post_id"))
            except (TypeError, ValueError):
                return self.send(400, {"error": "post_id가 정수가 아니다."})
            return self.send(200, {"job": start(inquiry_job, pid)})
        return self.send(404, {"error": "없는 경로다."})


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="아이템 ①, ② 시연 웹 화면")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"시연 화면: http://127.0.0.1:{a.port}  (끝내려면 Ctrl+C)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
