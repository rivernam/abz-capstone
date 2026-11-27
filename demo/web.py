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


class RecordingLLM:
    """ClaudeJSON을 감싸 요청과 응답을 대화 이벤트로 남긴다."""

    def __init__(self, jid):
        from runner.llm import ClaudeJSON
        self.inner, self.jid, self.n, self.system_sent = ClaudeJSON(), jid, 0, False

    def call(self, system, user, schema):
        if not self.system_sent:
            push(self.jid, {"type": "system", "text": system})
            self.system_sent = True
        try:
            pretty = json.dumps(json.loads(user), ensure_ascii=False, indent=2)
        except ValueError:
            pretty = user
        kind = "요약 다시 쓰기 요청(발행 전 검사)" if set(schema.get("properties", {})) == {"summary"} else "판단 요청"
        push(self.jid, {"type": "user", "text": pretty, "label": kind})
        out, usage = self.inner.call(system, user, schema)
        self.n += 1
        push(self.jid, {"type": "model", "n": self.n, "blocks": [{"type": "json", "text": json.dumps(out, ensure_ascii=False, indent=2)}],
                        "usage": usage})
        return out, usage


def report_job(jid, day):
    from runner.daily_report import run
    out = OUT / f"web_report_{jid}"
    r = run(day, llm=RecordingLLM(jid), publish=True, storage_dir=out / "storage", outbox=out / "outbox.jsonl", runs_dir=out / "runs")
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


CUSTOM_IDS = iter(range(1001, 10**6))
LIMITS = {"author": 30, "title": 100, "pc_field": 300, "body": 2000}


def stopped_pcs():
    """저장소 가상 세계에서 서비스가 멈춘 PC와 마지막 오류(읽기 전용). 직접 문의를 쓸 때 고를 수 있게 보여 준다."""
    from abzlib import virtual_pc as vpc
    from abzlib.master import load_master
    from abzlib.world import World
    w = World()
    master = load_master(w.db_path)
    out = []
    for d in sorted(w.pc_dir.iterdir()):
        try:
            svc = vpc.read_service(w, d.name)
        except Exception:
            continue
        if svc.get("status") == "stopped":
            rec = master.get(d.name, {})
            out.append({"pc_id": d.name, "process": rec.get("process", "미등록"), "since": svc.get("since"),
                        "error": vpc.last_error(w, d.name)})
    return out


def custom_post(body):
    """화면에서 쓴 문의. 길이만 제한하고 내용은 그대로 둔다(본문 지시를 따르지 않는 것은 에이전트와 Tool의 몫이다)."""
    p = {k: str(body.get(k, "")).strip()[:n] for k, n in LIMITS.items()}
    if not p["title"] or not p["pc_field"] or not p["body"]:
        raise ValueError("제목, 설비 PC ID, 본문을 모두 적어야 한다.")
    p.update(post_id=next(CUSTOM_IDS), author=p["author"] or "시연 사용자", category="직접 작성",
             created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return p


def inquiry_job(jid, p):
    from abzlib.targets import parse_pc_field
    from eval.score_inquiries import make_world
    from runner.inquiry_agent import run
    from tools import board
    pid = p["post_id"]
    out = OUT / f"web_inquiry_{jid}"
    world = make_world(paths.ROOT, out)
    board.create_post(world, p["author"], p["title"], p["pc_field"], p["body"], p["created_at"], post_id=pid)
    parsed = parse_pc_field(p["pc_field"], p["body"])
    pcs = [x for x in parsed["targets"] + parsed["body_only"] if (world.pc_dir / x).exists()]
    before = snapshot(world, pcs)
    from runner.inquiry_agent import system_prompt
    push(jid, {"type": "system", "text": system_prompt()})
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
    finish(jid, {"post_id": pid, "post": p, "status": r["status"], "model_calls": r["model_calls"], "tool_calls": r["tool_calls"],
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
        if path == "/api/stopped":
            return self.send(200, {"pcs": stopped_pcs()})
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
            if isinstance(body.get("custom"), dict):
                try:
                    p = custom_post(body["custom"])
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
                return self.send(200, {"job": start(inquiry_job, p), "post": p})
            posts = {p["post_id"]: p for p in json.loads(paths.INQUIRY_SET.read_text(encoding="utf-8"))["posts"]}
            try:
                p = posts[int(body.get("post_id"))]
            except (TypeError, ValueError, KeyError):
                return self.send(400, {"error": "시험 세트에 없는 post_id다."})
            return self.send(200, {"job": start(inquiry_job, p)})
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
