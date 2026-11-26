import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from abzlib.pcid import normalize_pc_id
from generator.make_world import generate
from tools.get_pc_info import get_pc_info


def digest(root):
    h = hashlib.sha256()
    for p in sorted(Path(root, "재료").rglob("*")):
        if p.is_file() and p.suffix != ".sqlite":
            h.update(p.name.encode() + p.read_bytes())
    return h.hexdigest()


def test_generation_is_deterministic(tmp_path):
    generate(tmp_path / "a")
    generate(tmp_path / "b")
    assert digest(tmp_path / "a") == digest(tmp_path / "b")


def test_world_shape(tmp_path):
    ans = generate(tmp_path)
    con = sqlite3.connect(tmp_path / "재료" / "db" / "기준정보.sqlite")
    assert con.execute("SELECT COUNT(*) FROM pc_master").fetchone()[0] == 300
    ids = {r[0] for r in con.execute("SELECT pc_id FROM pc_master")}
    assert not set(ans["orphans"]) & ids                       # 미등록 PC는 DB에 없다
    for pc, v in ans["ip_mismatch"].items():
        assert v["db_ip"] != v["log_ip"]
    flat = [pc for k, v in ans["scenarios"].items() for pc in v]
    assert len(flat) == len(set(flat))                         # 시나리오 PC는 서로 겹치지 않는다
    for day in ("2026-11-24", "2026-11-25", "2026-11-26"):
        assert (tmp_path / "재료" / "서버로그" / f"server_{day}.txt").exists()
    no_report = set(ans["scenarios"]["no_report"])
    log25 = (tmp_path / "재료" / "서버로그" / "server_2026-11-25.txt").read_text(encoding="utf-8")
    assert not any(pc in log25 for pc in no_report)           # 로그 한 줄도 없는 PC


def test_normalize():
    assert normalize_pc_id("pc-a0101") == "PC-A0101"
    assert normalize_pc_id(" PCA0101 ") == "PC-A0101"
    assert normalize_pc_id("PC-A101") is None
    assert normalize_pc_id(None) is None


def test_get_pc_info(tmp_path):
    ans = generate(tmp_path)
    db = tmp_path / "재료" / "db" / "기준정보.sqlite"
    r = get_pc_info("pc-a0101", db)
    assert r["found"] and r["pc_id"] == "PC-A0101" and r["normalized"] is True
    assert get_pc_info("PC-A0101", db)["normalized"] is False
    missing = get_pc_info(ans["orphans"][0], db)
    assert missing == {"found": False, "input_id": ans["orphans"][0], "pc_id": ans["orphans"][0], "reason": "not_registered"}
    assert get_pc_info("abc", db)["reason"] == "invalid_format"
    pc = next(iter(ans["ip_mismatch"]))
    assert get_pc_info(pc, db)["ip"] == ans["ip_mismatch"][pc]["db_ip"]
