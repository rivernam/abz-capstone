import csv
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from abzlib.pcid import normalize_pc_id
from generator.make_world import generate, key_name
from tools.get_pc_info import get_pc_info

D25 = "2026-11-25"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("world")
    ans = generate(root)
    op = json.loads((root / "기록" / "정답_PC원인.json").read_text(encoding="utf-8"))
    con = sqlite3.connect(root / "재료" / "db" / "기준정보.sqlite")
    con.row_factory = sqlite3.Row
    db = {r["pc_id"]: dict(r) for r in con.execute("SELECT * FROM pc_master")}
    con.close()
    logs = {}
    for f in sorted((root / "재료" / "서버로그").glob("server_*.txt")):
        logs[f.stem[7:]] = [l.split(" | ") for l in f.read_text(encoding="utf-8").splitlines()]
    return {"root": root, "ans": ans, "op": op, "db": db, "logs": logs}


def digest(root):
    h = hashlib.sha256()
    for p in sorted(Path(root, "재료").rglob("*")):
        if p.is_file() and p.suffix != ".sqlite":
            h.update(str(p.relative_to(root)).encode() + p.read_bytes())
    return h.hexdigest()


def test_generation_is_deterministic(tmp_path):
    generate(tmp_path / "a")
    generate(tmp_path / "b")
    assert digest(tmp_path / "a") == digest(tmp_path / "b")


def test_master_shape(world):
    db, ans = world["db"], world["ans"]
    assert len(db) == 300
    assert set(next(iter(db.values()))) == {"pc_id", "process", "equipment_model", "ip", "internal_ip",
                                            "client_version", "registered_at"}
    assert all(r["ip"] and r["equipment_model"] for r in db.values())
    via = [r for r in db.values() if r["internal_ip"]]
    assert 0.25 <= len(via) / 300 <= 0.35                     # 설비망 PC는 약 30%
    assert len({(r["ip"], r["internal_ip"]) for r in db.values()}) == 300        # 식별 키 (사내망 IP, 설비망 IP)는 유일
    combos = ans["proxy"]["combos"]
    kinds = {c["kind"]: c for c in combos.values()}
    assert db[kinds["direct"]["proxies"][0]]["internal_ip"] is None   # 직접 접속 프록시는 설비망 IP 미등록
    for px in kinds["self"]["proxies"] + kinds["split"]["proxies"]:
        assert db[px]["internal_ip"]                                   # 자기 자신을 거치는 프록시는 등록


def test_log_line_format(world):
    for day, rows in world["logs"].items():
        for r in rows:
            assert len(r) in (6, 7), r
            assert r[2] in {"HEARTBEAT", "START", "STOP", "ERROR", "IDENTIFY_FAIL"}
            assert (len(r) == 7) == (r[2] == "ERROR")
            assert (r[1] == "-") == (r[2] == "IDENTIFY_FAIL")


def test_identification_is_consistent(world):
    """서버는 (접속 주소, 설비망 IP)로 기준정보에서 PC를 찾는다. 기준정보 수정(11-24 23:30) 이후 로그로 확인."""
    key = {(r["ip"], r["internal_ip"] or "-"): pc for pc, r in world["db"].items()}
    fails = set()
    for d in ("2026-11-25", "2026-11-26"):                     # 수정 이후만 현재 기준정보와 맞는다
        for r in world["logs"][d]:
            if r[2] == "IDENTIFY_FAIL":
                assert (r[4], r[5]) not in key
                fails.add((r[4], r[5]))
            else:
                assert key[(r[4], r[5])] == r[1]
    assert fails == {(f["conn"], f["internal"]) for f in world["ans"]["identify_fail"]}


def identify_fail_reason(db, conn, internal):
    """구현명세 §5 식별 실패 사유 규칙. (사유, 특정된 PC 또는 None)."""
    same_internal = [pc for pc, r in db.items() if internal != "-" and r["internal_ip"] == internal]
    if same_internal:
        return "intranet_ip_typo", same_internal[0]
    if internal != "-" and any(r["ip"] == conn for r in db.values()):
        return "facility_ip_typo", None
    return "not_in_master", None


def test_identify_fail_reasons(world):
    for f in world["ans"]["identify_fail"]:
        reason, pc = identify_fail_reason(world["db"], f["conn"], f["internal"])
        assert reason == f["expected"], f
        if reason == "intranet_ip_typo":
            assert pc == f["pc_id"]


def anomalies(db, logs, day):
    """구현명세 §5 이상 PC 판정 규칙의 시험용 구현."""
    rows = [r for d in sorted(logs) for r in logs[d]]
    hb, starts, errors, seen = {}, {}, set(), set()
    for r in rows:
        pc = r[1]
        if pc not in db:
            continue
        ts = datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S")
        if r[0][:10] == day:
            seen.add(pc)
            if r[2] == "START":
                starts[pc] = starts.get(pc, 0) + 1
            if r[2] == "ERROR":
                errors.add(pc)
        if r[2] == "HEARTBEAT":
            hb.setdefault(pc, []).append(ts)
    out = {}
    for pc in db:
        cats = set()
        ts = sorted(hb.get(pc, []))
        if any(b - a >= timedelta(minutes=30) and f"{a:%Y-%m-%d}" == day for a, b in zip(ts, ts[1:])):
            cats.add("heartbeat_gap")
        if starts.get(pc, 0) >= 5:
            cats.add("restart_loop")
        if pc in errors:
            cats.add("error")
        if pc not in seen:
            cats.add("no_report")
        if cats:
            out[pc] = sorted(cats)
    return out


def test_anomalies_match_answer(world):
    for day in world["ans"]["report_dates"]:
        assert anomalies(world["db"], world["logs"], day) == world["ans"]["anomalies"][day], day


def test_planned_restart_is_not_anomaly(world):
    trap = set(world["ans"]["scenarios"]["planned_restart_midnight"])
    assert not trap & set(world["ans"]["anomalies"][D25])


def test_error_classification(world):
    pats = [r["message_pattern"] for r in csv.DictReader(
        open(world["root"] / "재료" / "문서" / "오류메시지패턴표.csv", encoding="utf-8"))]
    cls = world["ans"]["error_classification"]
    assert sum(v == "NEW" for v in cls.values()) == 5 and len(cls) == 12
    for msg, exp in cls.items():
        assert not any(msg.startswith(p) for p in pats)
        assert exp == "NEW" or exp in pats


def test_pc_folders(world):
    root, db, op = world["root"], world["db"], world["op"]
    defaults = list(csv.DictReader(open(root / "재료" / "문서" / "설정기본값표.csv", encoding="utf-8")))
    assert {(d["version"], d["key"]) for d in defaults} >= {("2.3.1", "server.host"), ("2.4.2", "server_host")}
    for pc, r in db.items():
        d = root / "재료" / "pc" / pc
        props = dict(l.split("=", 1) for l in (d / "client.properties").read_text(encoding="utf-8").splitlines()
                     if "=" in l)
        assert key_name(r["client_version"], "server_host") in props
        status = json.loads((d / "service.json").read_text(encoding="utf-8"))["status"]
        assert status == ("stopped" if "stopped_at" in op["pcs"].get(pc, {}) else "running")
        assert "client started" in (d / "client.log").read_text(encoding="utf-8")


def test_jvm_proxy_args(world):
    root, db, op, ans = world["root"], world["db"], world["op"], world["ans"]
    for pc, r in db.items():
        args = (root / "재료" / "pc" / pc / "jvm.args").read_text(encoding="utf-8")
        m = re.search(r"-Dabz\.proxy\.host=(\S+) -Dabz\.proxy\.port=(\d+)", args)
        cause = op["pcs"].get(pc, {}).get("cause")
        if cause in ("proxy_missing", "proxy_uncomputable"):
            assert m is None
        elif cause == "proxy_wrong":
            assert f"{m.group(1)}:{m.group(2)}" != op["pcs"][pc]["expected_proxy"]
        elif r["internal_ip"]:
            assert m and m.group(2) == str(ans["proxy"]["port"])
            props = (root / "재료" / "pc" / pc / "client.properties").read_text(encoding="utf-8")
            assert key_name(r["client_version"], "internal_ip_pattern") in props
        else:
            assert m is None


def proxy_by_peers(root, db, pc):
    """ADR 0004: 같은 사내망 IP를 쓰는 실행 중 설비망 PC가 3대 이상이고 프록시 값이 모두 같으면 그 값."""
    values = []
    for peer, r in db.items():
        if peer == pc or r["ip"] != db[pc]["ip"] or not r["internal_ip"]:
            continue
        d = root / "재료" / "pc" / peer
        if json.loads((d / "service.json").read_text(encoding="utf-8"))["status"] != "running":
            continue
        m = re.search(r"-Dabz\.proxy\.host=(\S+) -Dabz\.proxy\.port=(\d+)", (d / "jvm.args").read_text(encoding="utf-8"))
        values.append(f"{m.group(1)}:{m.group(2)}" if m else None)
    return values[0] if len(values) >= 3 and len(set(values)) == 1 else None


def test_proxy_is_computable_from_peers(world):
    root, db, op = world["root"], world["db"], world["op"]
    for pc, h in op["pcs"].items():
        if h["cause"] in ("proxy_missing", "proxy_wrong", "pattern_mismatch"):
            assert proxy_by_peers(root, db, pc) == h["expected_proxy"], pc
        elif h["cause"] in ("proxy_uncomputable", "proxy_down"):
            assert proxy_by_peers(root, db, pc) is None, pc


def test_normalize():
    assert normalize_pc_id("pc-a0101") == "PC-A0101"
    assert normalize_pc_id(" PCA0101 ") == "PC-A0101"
    assert normalize_pc_id("PC-A101") is None
    assert normalize_pc_id(None) is None


def test_get_pc_info(world):
    ans = world["ans"]
    db = world["root"] / "재료" / "db" / "기준정보.sqlite"
    r = get_pc_info("pc-a0101", db)
    assert r["found"] and r["pc_id"] == "PC-A0101" and r["normalized"] is True and "equipment_model" in r
    assert get_pc_info("PC-A0101", db)["normalized"] is False
    missing = get_pc_info("PC-C0199", db)
    assert missing == {"found": False, "input_id": "PC-C0199", "pc_id": "PC-C0199", "reason": "not_registered"}
    assert get_pc_info("abc", db)["reason"] == "invalid_format"
    typo = next(f for f in ans["identify_fail"] if "db_ip" in f)
    assert get_pc_info(typo["pc_id"], db)["ip"] == typo["db_ip"]
