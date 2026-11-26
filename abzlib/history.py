"""조치 이력(기록/조치이력.sqlite). 멱등 키 {post_id}:{tool}:{pc_id}. 회신 표와 채점의 근거다."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime

SCHEMA = """CREATE TABLE IF NOT EXISTS actions (
    idem_key TEXT PRIMARY KEY, post_id INTEGER NOT NULL, tool TEXT NOT NULL, pc_id TEXT NOT NULL,
    at TEXT NOT NULL, before TEXT, after TEXT, result TEXT NOT NULL, detail TEXT)"""


def key(post_id, tool, pc_id):
    return f"{post_id}:{tool}:{pc_id}"


def _con(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute(SCHEMA)
    return con


def _row(r):
    d = dict(r)
    for k in ("before", "after", "detail"):
        d[k] = json.loads(d[k]) if d[k] else None
    return d


def get(world, idem_key):
    with closing(_con(world.history_db)) as con:
        r = con.execute("SELECT * FROM actions WHERE idem_key = ?", (idem_key,)).fetchone()
    return _row(r) if r else None


def record(world, post_id, tool, pc_id, result, before=None, after=None, detail=None):
    rec = {"idem_key": key(post_id, tool, pc_id), "post_id": post_id, "tool": tool, "pc_id": pc_id,
           "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "before": before, "after": after,
           "result": result, "detail": detail}
    with closing(_con(world.history_db)) as con, con:
        con.execute("INSERT INTO actions VALUES (:idem_key,:post_id,:tool,:pc_id,:at,:before,:after,:result,:detail)",
                    {**rec, **{k: json.dumps(rec[k], ensure_ascii=False) if rec[k] is not None else None
                               for k in ("before", "after", "detail")}})
    return rec


def rows(world, post_id=None):
    with closing(_con(world.history_db)) as con:
        if post_id is None:
            rs = con.execute("SELECT * FROM actions ORDER BY rowid").fetchall()
        else:
            rs = con.execute("SELECT * FROM actions WHERE post_id = ? ORDER BY rowid", (post_id,)).fetchall()
    return [_row(r) for r in rs]
