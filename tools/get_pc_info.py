"""get_pc_info: PC ID로 기준정보 DB를 조회한다 (읽기 전용).

사용: python tools/get_pc_info.py PC-A0101
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from abzlib import paths
from abzlib.pcid import normalize_pc_id


def get_pc_info(pc_id, db_path=None):
    norm = normalize_pc_id(pc_id)
    if norm is None:
        return {"found": False, "input_id": pc_id, "pc_id": None, "reason": "invalid_format"}
    con = sqlite3.connect(f"file:{db_path or paths.DB_PATH}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM pc_master WHERE pc_id = ?", (norm,)).fetchone()
    finally:
        con.close()
    if row is None:
        return {"found": False, "input_id": pc_id, "pc_id": norm, "reason": "not_registered"}
    return {"found": True, "input_id": pc_id, "normalized": pc_id != norm, **dict(row)}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        print("usage: python tools/get_pc_info.py <PC ID>")
        sys.exit(1)
    print(json.dumps(get_pc_info(sys.argv[1]), ensure_ascii=False, indent=2))
