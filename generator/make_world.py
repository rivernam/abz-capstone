"""가상 세계 생성기: 기준정보 DB, 서버 실행 로그, 오류 메시지 패턴표, 정답표를 만든다.

시드가 고정되어 있어 몇 번을 돌려도 같은 결과가 나온다. 모든 데이터는 가상이다.
사용: python generator/make_world.py
"""
import csv
import json
import random
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from abzlib import paths

SEED = 20261126
PROCESSES = {"A": "ETCH", "B": "CLEAN", "C": "DEPO", "D": "LITHO", "E": "TEST"}
PER_PROCESS = 60
VERSIONS = ["2.3.1", "2.4.0", "2.4.2"]
START = datetime(2026, 11, 24, 0, 0, 0)
END = datetime(2026, 11, 26, 8, 0, 0)          # 리포트 시각(오전 8시) 직전까지
SLOT = timedelta(minutes=10)                   # 정상 신호 간격
D24, D25 = "2026-11-24", "2026-11-25"

# (메시지 앞부분, 원인, 권장 조치, 자동 조치 가능)
PATTERNS = [
    ("connection refused", "서버 접속 거부(서버 주소나 방화벽 문제)", "server_host 확인, 서버 상태 확인", "no"),
    ("config parse failed", "클라이언트 설정 값 오류", "해당 설정 키를 기본값으로 복구하고 서비스 재시작", "yes"),
    ("java.lang.OutOfMemoryError", "JVM 힙 메모리 부족", "java 실행 인자 -Xmx 상향 검토(운영자 확인)", "no"),
    ("license check timeout", "라이선스 서버 응답 지연", "라이선스 서버 상태 확인", "no"),
    ("bind failed", "포트 충돌", "포트를 쓰는 다른 프로세스 확인", "no"),
    ("heartbeat send failed", "서버 통신 지연", "네트워크 상태 확인", "no"),
]
ERROR_MESSAGES = [
    "connection refused: 10.20.0.5:8443",
    "config parse failed: invalid value for retry_count",
    "java.lang.OutOfMemoryError: Java heap space",
    "license check timeout after 30s",
    "bind failed: port 8443 already in use",
    "heartbeat send failed: timeout",
]


def swap_digits(ip):
    """기준정보 오입력 재현: 마지막 옥텟의 숫자 순서를 바꾼다."""
    head, last = ip.rsplit(".", 1)
    flipped = last[::-1]
    if flipped == last:
        flipped = str(int(last) + 1)
    return f"{head}.{flipped}"


def generate(root=None):
    root = Path(root) if root else paths.ROOT
    material = root / "재료"
    rng = random.Random(SEED)

    # ---- 1. PC 마스터 ----
    pcs = []
    for pi, (letter, proc) in enumerate(PROCESSES.items()):
        for i in range(PER_PROCESS):
            pcs.append({
                "pc_id": f"PC-{letter}{101 + i:04d}",
                "process": proc,
                "ip": f"10.{20 + pi}.1.{i + 10}",
                "internal_ip": f"192.168.{pi}.{i + 10}",
                "client_version": rng.choices(VERSIONS, weights=[2, 3, 5])[0],
                "registered_at": f"2025-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            })
    pool = [p["pc_id"] for p in pcs]
    rng.shuffle(pool)

    def take(n):
        out, pool[:] = pool[:n], pool[n:]
        return out

    sc = {
        "gap_day": take(5), "gap_midnight": take(2), "restart_loop": take(4),
        "error_only": take(6), "error_gap": take(2), "no_report": take(5),
        "ip_mismatch": take(4), "planned_restart_midnight": take(10), "prev_error": take(3),
    }
    orphans = [
        {"pc_id": "PC-C0199", "ip": "10.22.9.99", "client_version": "2.4.0"},
        {"pc_id": "PC-D0198", "ip": "10.23.9.98", "client_version": "2.3.1"},
    ]

    # ---- 2. 정답표용 기록 ----
    cats = {D24: {}, D25: {}}

    def mark(day, pc, cat):
        cats[day].setdefault(pc, set()).add(cat)

    plans = {p["pc_id"]: {"skip": [], "extra": [], "cut": None} for p in pcs}
    for o in orphans:
        plans[o["pc_id"]] = {"skip": [], "extra": [], "cut": None}

    def t(day, hh, mm, ss=0):
        y, m, d = map(int, day.split("-"))
        return datetime(y, m, d, hh, mm, ss)

    for pc in sc["gap_day"]:
        s = t(D25, rng.randint(6, 18), 0)
        plans[pc]["skip"].append((s, s + timedelta(hours=rng.randint(1, 4))))
        mark(D25, pc, "heartbeat_gap")
    for pc in sc["gap_midnight"]:
        plans[pc]["skip"].append((t(D25, 23, 40), t("2026-11-26", 0, 10)))
        mark(D25, pc, "heartbeat_gap")
    for pc in sc["restart_loop"]:
        base = t(D25, rng.randint(8, 16), 5)
        for n in range(rng.randint(6, 8)):
            plans[pc]["extra"] += [(base + timedelta(minutes=7 * n), "STOP", ""),
                                   (base + timedelta(minutes=7 * n + 1), "START", "")]
        mark(D25, pc, "restart_loop")
    for pc in sc["error_only"]:
        for _ in range(rng.randint(1, 3)):
            plans[pc]["extra"].append((t(D25, rng.randint(6, 21), rng.randint(0, 59), rng.randint(0, 59)),
                                       "ERROR", rng.choice(ERROR_MESSAGES)))
        mark(D25, pc, "error")
    for pc in sc["error_gap"]:
        at = t(D25, rng.randint(7, 17), rng.randint(0, 59))
        plans[pc]["extra"].append((at, "ERROR", "license check timeout after 30s"))
        plans[pc]["skip"].append((at + timedelta(minutes=1), at + timedelta(hours=2)))
        mark(D25, pc, "error")
        mark(D25, pc, "heartbeat_gap")
    for pc in sc["no_report"]:
        plans[pc]["cut"] = t(D25, 0, 0)
        mark(D25, pc, "no_report")
    for pc in sc["planned_restart_midnight"]:
        plans[pc]["skip"].append((t("2026-11-25", 0, 0), t("2026-11-25", 0, 5)))
        plans[pc]["extra"] += [(t(D24, 23, 55, 10), "STOP", ""), (t(D25, 0, 3, 20), "START", "")]
    for pc in sc["prev_error"]:
        plans[pc]["extra"].append((t(D24, rng.randint(6, 21), rng.randint(0, 59), 5), "ERROR", rng.choice(ERROR_MESSAGES)))
        mark(D24, pc, "error")
    for pc in sc["error_only"][:2]:                       # 이틀 연속 오류(지속 건)
        plans[pc]["extra"].append((t(D24, 10, 30, 0), "ERROR", rng.choice(ERROR_MESSAGES)))
        mark(D24, pc, "error")

    # ---- 3. 서버 실행 로그 ----
    by_pc = {p["pc_id"]: p for p in pcs}
    wrong_db_ip = {}
    n_slots = int((END - START) / SLOT)
    lines = []                                            # (ts, pc, text)
    all_pcs = [(p["pc_id"], p["client_version"], p["ip"]) for p in pcs] + \
              [(o["pc_id"], o["client_version"], o["ip"]) for o in orphans]
    for pc, ver, ip in all_pcs:
        plan = plans[pc]
        events = []
        for k in range(n_slots):
            slot = START + k * SLOT
            jitter = rng.randint(0, 40)
            if plan["cut"] and slot >= plan["cut"]:
                continue
            if any(a <= slot < b for a, b in plan["skip"]):
                continue
            events.append((slot + timedelta(seconds=jitter), "HEARTBEAT", ""))
        events += plan["extra"]
        for ts, ev, msg in events:
            if ts >= END:
                continue
            text = f"{ts:%Y-%m-%d %H:%M:%S} | {pc} | {ev} | {ver} | {ip}" + (f" | {msg}" if msg else "")
            lines.append((ts, pc, text))
    lines.sort(key=lambda x: (x[0], x[1]))
    log_dir = material / "서버로그"
    log_dir.mkdir(parents=True, exist_ok=True)
    for old in log_dir.glob("server_*.txt"):
        old.unlink()
    per_day = {}
    for ts, _, text in lines:
        per_day.setdefault(f"{ts:%Y-%m-%d}", []).append(text)
    for day, ls in per_day.items():
        (log_dir / f"server_{day}.txt").write_text("\n".join(ls) + "\n", encoding="utf-8")

    # ---- 4. 기준정보 DB (오입력 IP 반영) ----
    for pc in sc["ip_mismatch"]:
        wrong_db_ip[pc] = {"db_ip": swap_digits(by_pc[pc]["ip"]), "log_ip": by_pc[pc]["ip"]}
        by_pc[pc]["ip"] = wrong_db_ip[pc]["db_ip"]
    db_dir = material / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "기준정보.sqlite"
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE pc_master (pc_id TEXT PRIMARY KEY, process TEXT NOT NULL, ip TEXT NOT NULL,
                   internal_ip TEXT NOT NULL, client_version TEXT NOT NULL, registered_at TEXT NOT NULL)""")
    con.executemany("INSERT INTO pc_master VALUES (:pc_id,:process,:ip,:internal_ip,:client_version,:registered_at)", pcs)
    con.commit()
    con.close()

    # ---- 5. 오류 메시지 패턴표 ----
    docs = material / "문서"
    docs.mkdir(parents=True, exist_ok=True)
    with open(docs / "오류메시지패턴표.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["message_pattern", "cause", "recommended_action", "auto_fixable"])
        w.writerows(PATTERNS)

    # ---- 6. 정답표 ----
    rec = root / "기록"
    rec.mkdir(parents=True, exist_ok=True)
    answer = {
        "seed": SEED, "report_date": D25, "total_pcs_in_db": len(pcs),
        "anomalies": {d: {pc: sorted(c) for pc, c in sorted(m.items())} for d, m in cats.items()},
        "scenarios": {k: sorted(v) for k, v in sc.items()},
        "orphans": [o["pc_id"] for o in orphans],
        "ip_mismatch": wrong_db_ip,
        "rules": {"heartbeat_gap_minutes": 30, "restart_loop_start_count": 5,
                  "gap_belongs_to_date_of_earlier_heartbeat": True,
                  "no_report": "DB에 있는 PC인데 해당 날짜 로그가 한 줄도 없음(전날에는 있었음)"},
    }
    (rec / "정답표_일일리포트.json").write_text(json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8")
    return answer


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    a = generate()
    print("PC", a["total_pcs_in_db"], "| 2026-11-25 이상 PC", len(a["anomalies"][D25]),
          "| 미등록", a["orphans"], "| IP 오입력", len(a["ip_mismatch"]))
