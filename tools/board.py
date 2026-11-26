"""가상 게시판: 문의 등록, 조회, 운영자 전용 메모, 정적 HTML 화면 (구현명세 §4 가상 게시판).

notes.visibility는 'operator'로 고정한다. 코드에 다른 값을 넣는 경로가 없고, 테이블 CHECK 제약도 건다.
검토 전 회신 초안이 사용자에게 보이면 안 되기 때문이다.

사용:
  python -m tools.board add --author 홍길동 --title "PC가 안 켜져요" --pcs "PC-A0101~0105" --body "..."
  python -m tools.board list
  python -m tools.board show 3
  python -m tools.board notes 3
  python -m tools.board html
"""
import argparse
import html
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from abzlib.world import World

VISIBILITY = "operator"
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS posts (
        post_id INTEGER PRIMARY KEY, author TEXT NOT NULL, title TEXT NOT NULL, pc_field TEXT NOT NULL,
        body TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT '접수')""",
    """CREATE TABLE IF NOT EXISTS notes (
        note_id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER NOT NULL REFERENCES posts(post_id),
        visibility TEXT NOT NULL DEFAULT 'operator' CHECK (visibility = 'operator'),
        body TEXT NOT NULL, idem_key TEXT UNIQUE, created_at TEXT NOT NULL)""",
]


def _con(world):
    world.board_db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(world.board_db)
    con.row_factory = sqlite3.Row
    for s in SCHEMA:
        con.execute(s)
    return con


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def create_post(world, author, title, pc_field, body, created_at=None, post_id=None):
    with closing(_con(world)) as con, con:
        cur = con.execute("INSERT INTO posts (post_id, author, title, pc_field, body, created_at) VALUES (?,?,?,?,?,?)",
                          (post_id, author, title, pc_field, body, created_at or _now()))
        return cur.lastrowid


def get_post(world, post_id):
    with closing(_con(world)) as con:
        r = con.execute("SELECT * FROM posts WHERE post_id = ?", (post_id,)).fetchone()
    return dict(r) if r else None


def list_posts(world):
    with closing(_con(world)) as con:
        return [dict(r) for r in con.execute("SELECT * FROM posts ORDER BY post_id")]


def add_operator_note(world, post_id, body, idem_key):
    """운영자 전용 메모를 단다. 같은 멱등 키가 있으면 새로 쓰지 않고 기존 메모를 돌려준다."""
    with closing(_con(world)) as con, con:
        old = con.execute("SELECT * FROM notes WHERE idem_key = ?", (idem_key,)).fetchone()
        if old:
            return {**dict(old), "replayed": True}
        cur = con.execute("INSERT INTO notes (post_id, visibility, body, idem_key, created_at) VALUES (?, ?, ?, ?, ?)",
                          (post_id, VISIBILITY, body, idem_key, _now()))
        r = con.execute("SELECT * FROM notes WHERE note_id = ?", (cur.lastrowid,)).fetchone()
    return {**dict(r), "replayed": False}


def list_notes(world, post_id=None):
    with closing(_con(world)) as con:
        if post_id is None:
            return [dict(r) for r in con.execute("SELECT * FROM notes ORDER BY note_id")]
        return [dict(r) for r in con.execute("SELECT * FROM notes WHERE post_id = ? ORDER BY note_id", (post_id,))]


CSS = """body{font-family:'Malgun Gothic',sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#222}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}
.muted{color:#777}.note{background:#fff7e0;border:1px solid #e8c96a;padding:10px 14px;margin:12px 0}
.op{background:#8a5a00;color:#fff;padding:2px 8px;border-radius:3px;font-size:12px}pre{white-space:pre-wrap;margin:0}"""


def _page(title, body):
    return (f"<!doctype html><html lang=ko><head><meta charset=utf-8><title>{html.escape(title)}</title>"
            f"<style>{CSS}</style></head><body>{body}</body></html>")


def render_html(world, out_dir=None):
    """정적 화면을 만든다. public/은 사용자 화면(메모 없음), operator/는 운영자 화면(운영자 전용 메모 포함)."""
    out = Path(out_dir) if out_dir else world.board_html_dir
    e = html.escape
    posts = list_posts(world)
    notes = {}
    for n in list_notes(world):
        notes.setdefault(n["post_id"], []).append(n)
    for view in ("public", "operator"):
        d = out / view
        d.mkdir(parents=True, exist_ok=True)
        rows = "".join(
            f"<tr><td>{p['post_id']}</td><td><a href='post_{p['post_id']}.html'>{e(p['title'])}</a></td><td>{e(p['author'])}</td>"
            f"<td>{e(p['pc_field'])}</td><td>{e(p['created_at'])}</td><td>{e(p['status'])}</td>"
            + (f"<td>{len(notes.get(p['post_id'], []))}</td>" if view == "operator" else "") + "</tr>" for p in posts)
        head = "<tr><th>번호</th><th>제목</th><th>작성자</th><th>설비 PC ID</th><th>작성 시각</th><th>상태</th>" + \
               ("<th>운영자 메모</th>" if view == "operator" else "") + "</tr>"
        label = "운영자 화면" if view == "operator" else "문의 게시판"
        (d / "index.html").write_text(_page(label, f"<h1>{label}</h1><table>{head}{rows}</table>"), encoding="utf-8")
        for p in posts:
            body = (f"<p><a href='index.html'>목록</a></p><h1>{e(p['title'])}</h1>"
                    f"<p class=muted>#{p['post_id']} · {e(p['author'])} · {e(p['created_at'])} · {e(p['status'])}</p>"
                    f"<p><b>설비 PC ID</b> {e(p['pc_field'])}</p><pre>{e(p['body'])}</pre>")
            if view == "operator":
                ns = notes.get(p["post_id"], [])
                body += "<h2>운영자 전용 메모</h2>" + ("".join(
                    f"<div class=note><span class=op>운영자 전용</span> <span class=muted>{e(n['created_at'])} · {e(n['idem_key'] or '')}</span>"
                    f"<pre>{e(n['body'])}</pre></div>" for n in ns) or "<p class=muted>메모가 없다.</p>")
            (d / f"post_{p['post_id']}.html").write_text(_page(p["title"], body), encoding="utf-8")
    return str(out)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="가상 게시판")
    ap.add_argument("--root", help="가상 세계 루트(기본: 저장소)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--author", required=True)
    a.add_argument("--title", required=True)
    a.add_argument("--pcs", required=True, help="설비 PC ID 필드")
    a.add_argument("--body", required=True)
    sub.add_parser("list")
    s = sub.add_parser("show")
    s.add_argument("post_id", type=int)
    n = sub.add_parser("notes")
    n.add_argument("post_id", type=int)
    sub.add_parser("html")
    args = ap.parse_args()
    w = World(args.root)
    if args.cmd == "add":
        print(json.dumps({"post_id": create_post(w, args.author, args.title, args.pcs, args.body)}, ensure_ascii=False))
    elif args.cmd == "list":
        for p in list_posts(w):
            print(f"#{p['post_id']} [{p['status']}] {p['title']} | {p['pc_field']} | {p['author']} {p['created_at']}")
    elif args.cmd == "show":
        print(json.dumps(get_post(w, args.post_id), ensure_ascii=False, indent=2))
    elif args.cmd == "notes":
        for x in list_notes(w, args.post_id):
            print(f"--- 메모 #{x['note_id']} ({x['visibility']}, {x['created_at']}, {x['idem_key']})\n{x['body']}")
    elif args.cmd == "html":
        print(render_html(w))


if __name__ == "__main__":
    main()
