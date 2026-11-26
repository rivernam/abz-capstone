"""기준정보 DB 읽기."""
import sqlite3


def load_master(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        return {r["pc_id"]: dict(r) for r in con.execute("SELECT * FROM pc_master ORDER BY pc_id")}
    finally:
        con.close()
