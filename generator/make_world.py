"""가상 세계 생성기.

기준정보 DB, 서버 실행 로그, 가상 PC 폴더, 문서 자료(패턴표, 설정기본값표), 정답 자료를 만든다.
시드가 고정되어 있어 몇 번을 돌려도 같은 결과가 나온다. 모든 데이터는 가상이다.
사용: python generator/make_world.py
"""
import csv
import json
import random
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from abzlib import paths

SEED = 20261126
PROCESSES = {"A": "ETCH", "B": "CLEAN", "C": "DEPO", "D": "LITHO", "E": "TEST"}
MODELS = {"ETCH": ["ET-300", "ET-500"], "CLEAN": ["CL-200", "CL-400"], "DEPO": ["DP-100", "DP-300"],
          "LITHO": ["LT-700", "LT-900"], "TEST": ["TS-110", "TS-210"]}
PER_PROCESS = 60
VERSIONS = ["2.3.1", "2.4.0", "2.4.2"]
START = datetime(2026, 11, 24, 0, 0, 0)
END = datetime(2026, 11, 26, 8, 0, 0)          # 리포트 시각(오전 8시) 직전까지
SLOT = timedelta(minutes=10)                   # 정상 신호 간격
D24, D25, D26 = "2026-11-24", "2026-11-25", "2026-11-26"
SERVER_HOST, SERVER_PORT, PROXY_PORT = "10.10.0.5", 8443, 3128

# 설비망을 쓰는 (공정, 설비 모델) 조합과 프록시 구성 방식
#   self   : 프록시 PC 하나가 설비망 IP를 등록하고 자기 자신을 거친다.
#   direct : 프록시 PC가 사내망 IP만 등록하고 직접 접속한다(프록시 값을 계산할 수 없음).
#   split  : 예외 조합. 프록시 PC 두 대가 나눠 맡는다(두 번째 프록시는 5대만 맡음).
PROXY_COMBOS = {("ETCH", "ET-300"): "self", ("DEPO", "DP-300"): "direct", ("LITHO", "LT-700"): "split"}
SPLIT_MINOR = 5

# (message_pattern, cause, recommended_action, auto_action)
PATTERNS = [
    ("connection refused", "서버 접속 거부(서버 주소, 방화벽, 프록시 문제)", "server_host와 프록시 인자 확인, 서버 상태 확인", "proxy_check_restart"),
    ("config parse failed", "클라이언트 설정 값 오류", "해당 설정 키를 기본값으로 복구하고 서비스 재시작", "config_reset_restart"),
    ("java.lang.OutOfMemoryError", "JVM 힙 메모리 부족", "java 실행 인자 -Xmx 상향 검토(운영자 확인)", "none"),
    ("license check timeout", "라이선스 서버 응답 지연", "라이선스 서버 상태 확인", "restart"),
    ("bind failed", "포트 충돌", "포트를 쓰는 다른 프로세스 확인", "none"),
    ("heartbeat send failed", "서버 통신 지연", "네트워크 상태 확인", "restart"),
]
# 설정 키(표준 이름, 기본값). 2.3.1은 점 표기, 2.4.x는 밑줄 표기를 쓴다.
CONFIG_KEYS = [("server_host", SERVER_HOST), ("server_port", str(SERVER_PORT)),
               ("heartbeat_interval_sec", "600"), ("log_level", "INFO"), ("retry_count", "3")]
STD_MESSAGES = {
    "connection refused": f"connection refused: {SERVER_HOST}:{SERVER_PORT}",
    "config parse failed": "config parse failed: key={key} value={value}",
    "java.lang.OutOfMemoryError": "java.lang.OutOfMemoryError: Java heap space",
    "license check timeout": "license check timeout after 30s",
    "bind failed": f"bind failed: port {SERVER_PORT} already in use",
    "heartbeat send failed": "heartbeat send failed: timeout",
}
# 패턴표와 표기만 다른 변형(정답: 해당 패턴)과 완전히 새로운 메시지(정답: NEW)
VARIANT_MESSAGES = {
    "Connection refused: connect": "connection refused",
    "OutOfMemoryError: Java heap space": "java.lang.OutOfMemoryError",
    "failed to parse config: retry_count=abc": "config parse failed",
    "license server timeout (30000ms)": "license check timeout",
}
NEW_MESSAGES = ["SSL handshake failed: certificate expired", "disk full: cannot write spool file"]


def key_name(version, key):
    return key.replace("_", ".") if version == "2.3.1" else key


def match_pattern(message):
    """패턴표 매칭 규칙: 메시지가 message_pattern으로 시작하면(대소문자 구분) 그 패턴이다."""
    for p, *_ in PATTERNS:
        if message.startswith(p):
            return p
    return None


def swap_digits(ip):
    """기준정보 오입력 재현: 마지막 옥텟의 숫자 순서를 바꾼다."""
    head, last = ip.rsplit(".", 1)
    flipped = last[::-1]
    if flipped == last:
        flipped = str(int(last) + 1)
    return f"{head}.{flipped}"


def t(day, hh, mm, ss=0):
    y, m, d = map(int, day.split("-"))
    return datetime(y, m, d, hh, mm, ss)


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
                "equipment_model": rng.choice(MODELS[proc]),
                "ip": f"10.{20 + pi}.1.{i + 10}",
                "phys_internal": f"192.168.{pi}.{i + 10}",     # 실제 설비망 주소(등록 여부와 별개)
                "internal_ip": None,
                "client_version": rng.choices(VERSIONS, weights=[2, 3, 5])[0],
                "registered_at": f"2025-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            })
    by_pc = {p["pc_id"]: p for p in pcs}
    real_ip = {p["pc_id"]: p["ip"] for p in pcs}          # 로그에 찍히는 실제 사내망 IP

    # ---- 2. 프록시 구성 ----
    route = {p["pc_id"]: p["pc_id"] for p in pcs}          # 각 PC가 서버에 닿을 때 거치는 PC(직접 접속은 자기 자신)
    proxy_of = {}                                          # 설비망 PC -> 프록시 PC (jvm.args에 들어갈 값의 출처)
    combos = {}
    for (proc, model), kind in PROXY_COMBOS.items():
        members = sorted(p["pc_id"] for p in pcs if p["process"] == proc and p["equipment_model"] == model)
        rng.shuffle(members)
        proxies = [members[0]] if kind != "split" else [members[0], members[1]]
        for pc in members:
            by_pc[pc]["internal_ip"] = by_pc[pc]["phys_internal"]
        assign = {}
        if kind == "split":
            minor = members[2:2 + SPLIT_MINOR]
            for pc in members:
                assign[pc] = proxies[1] if (pc == proxies[1] or pc in minor) else proxies[0]
        else:
            for pc in members:
                assign[pc] = proxies[0]
        if kind == "direct":                               # 프록시 PC는 사내망 IP만 등록하고 직접 접속
            by_pc[proxies[0]]["internal_ip"] = None
            del assign[proxies[0]]
        for pc, px in assign.items():
            proxy_of[pc] = px
            route[pc] = px
        combos[f"{proc}/{model}"] = {"kind": kind, "proxies": proxies, "members": sorted(members)}
    proxy_pcs = {px for c in combos.values() for px in c["proxies"]}

    def proxy_value(pc):
        px = proxy_of[pc]
        return f"{by_pc[px]['phys_internal']}:{PROXY_PORT}"

    # ---- 3. 시나리오 PC 고르기 ----
    pool = [p["pc_id"] for p in pcs if p["pc_id"] not in proxy_pcs]
    rng.shuffle(pool)

    def take(n, pred=lambda pc: True):
        out = [pc for pc in pool if pred(pc)][:n]
        if len(out) < n:
            raise RuntimeError("시나리오 PC가 모자란다")
        for pc in out:
            pool.remove(pc)
        return out

    direct = lambda pc: pc not in proxy_of
    combo_of = {pc: k for k, c in combos.items() for pc in c["members"]}
    self_combo = next(k for k, c in combos.items() if c["kind"] == "self")
    direct_combo = next(k for k, c in combos.items() if c["kind"] == "direct")
    split_combo = next(k for k, c in combos.items() if c["kind"] == "split")
    split_major, split_minor_px = combos[split_combo]["proxies"]
    split_minor = sorted(pc for pc, px in proxy_of.items() if px == split_minor_px and pc != split_minor_px)

    for pc in split_minor:                                 # 프록시 장애 묶음 대상은 따로 둔다
        if pc in pool:
            pool.remove(pc)
    sc = {
        # 제약이 있는 시나리오부터 고른다
        "cluster_license": take(4, lambda pc: direct(pc) and by_pc[pc]["client_version"] == "2.3.1"),
        "cluster_coincidental": take(3, lambda pc: direct(pc) and by_pc[pc]["process"] == "TEST"),
        "route_bypass": take(1, lambda pc: combo_of.get(pc) == self_combo),
        "route_unknown_source": take(1, direct),
        "ip_mismatch": take(4, direct),
        "op_proxy_missing": take(2, lambda pc: combo_of.get(pc) == self_combo),
        "op_proxy_wrong": take(1, lambda pc: combo_of.get(pc) == self_combo),
        "op_proxy_uncomputable": take(1, lambda pc: combo_of.get(pc) == direct_combo),
        "op_proxy_split_major": take(1, lambda pc: proxy_of.get(pc) == split_major),
        # 일반 시나리오
        "gap_day": take(5), "gap_midnight": take(2), "restart_loop": take(4),
        "error_only": take(6), "error_gap": take(2), "no_report": take(5),
        "planned_restart_midnight": take(10), "prev_error": take(3),
        "op_config": take(3, lambda pc: True),
        "op_transient": take(3), "op_server_down": take(1), "op_oom": take(1), "op_bind": take(1),
    }
    # op_config에 2.3.1이 하나는 들어가게 한다(버전별 키 이름 확인용)
    if not any(by_pc[pc]["client_version"] == "2.3.1" for pc in sc["op_config"]):
        sc["op_config"][-1] = take(1, lambda pc: by_pc[pc]["client_version"] == "2.3.1")[0]
    sc["cluster_proxy_down"] = [split_minor_px] + split_minor
    sc["op_proxy_down"] = [split_minor[0]]
    orphans = [
        {"pc_id": "PC-C0199", "ip": "10.22.9.99", "client_version": "2.4.0"},
        {"pc_id": "PC-D0198", "ip": "10.23.9.98", "client_version": "2.3.1"},
    ]

    # ---- 4. 사건 계획 ----
    cats = {D24: {}, D25: {}}

    def mark(day, pc, cat):
        cats[day].setdefault(pc, set()).add(cat)

    plans = {pc: {"skip": [], "extra": [], "cut": None, "client": []} for pc in by_pc}
    for o in orphans:
        plans[o["pc_id"]] = {"skip": [], "extra": [], "cut": None, "client": []}

    def err(pc, at, msg, day):
        plans[pc]["extra"].append((at, "ERROR", msg))
        mark(day, pc, "error")

    for pc in sc["gap_day"]:
        s = t(D25, rng.randint(6, 18), 0)
        plans[pc]["skip"].append((s, s + timedelta(hours=rng.randint(1, 4))))
        mark(D25, pc, "heartbeat_gap")
    for pc in sc["gap_midnight"]:
        plans[pc]["skip"].append((t(D25, 23, 40), t(D26, 0, 10)))
        mark(D25, pc, "heartbeat_gap")
    for pc in sc["restart_loop"]:
        base = t(D25, rng.randint(8, 16), 5)
        for n in range(rng.randint(6, 8)):
            plans[pc]["extra"] += [(base + timedelta(minutes=7 * n), "STOP", ""),
                                   (base + timedelta(minutes=7 * n + 1), "START", "")]
        mark(D25, pc, "restart_loop")
    odd_messages = list(VARIANT_MESSAGES) + NEW_MESSAGES
    rng.shuffle(odd_messages)
    for pc, msg in zip(sc["error_only"], odd_messages):    # 오류 메시지 분류 시험용
        err(pc, t(D25, rng.randint(6, 21), rng.randint(0, 59), rng.randint(0, 59)), msg, D25)
    for pc in sc["error_gap"]:
        at = t(D25, rng.randint(7, 17), rng.randint(0, 59))
        err(pc, at, STD_MESSAGES["license check timeout"], D25)
        plans[pc]["skip"].append((at + timedelta(minutes=1), at + timedelta(hours=2)))
        mark(D25, pc, "heartbeat_gap")
    for pc in sc["no_report"]:
        plans[pc]["cut"] = t(D25, 0, 0)
        mark(D25, pc, "no_report")
    for pc in sc["planned_restart_midnight"]:
        plans[pc]["skip"].append((t(D25, 0, 0), t(D25, 0, 5)))
        plans[pc]["extra"] += [(t(D24, 23, 55, 10), "STOP", ""), (t(D25, 0, 3, 20), "START", "")]
    std = [m for p, m in STD_MESSAGES.items() if p != "config parse failed"]
    for pc in sc["prev_error"]:
        err(pc, t(D24, rng.randint(6, 21), rng.randint(0, 59), 5), rng.choice(std), D24)
    for pc in sc["error_only"][:2]:                       # 이틀 연속 오류(지속 건)
        err(pc, t(D24, 10, 30, 0), rng.choice(std), D24)

    # 진짜 묶음 1: 같은 버전(2.3.1)에서 license timeout이 09~10시대에 몰림
    for k, pc in enumerate(sc["cluster_license"]):
        err(pc, t(D25, 9, 10, rng.randint(0, 59)) + timedelta(minutes=20 * k), STD_MESSAGES["license check timeout"], D25)
    # 진짜 묶음 2: 프록시 PC 장애. 프록시와 그 프록시를 쓰는 PC들이 같은 시간대에 끊기고,
    # 재접속 후 클라이언트가 버퍼에 쌓인 connection refused를 보고한다.
    out_a, out_b = t(D25, 10, 0), t(D25, 11, 20)
    px_addr = f"{by_pc[split_minor_px]['phys_internal']}:{PROXY_PORT}"
    for pc in sc["cluster_proxy_down"]:
        plans[pc]["skip"].append((out_a, out_b))
        mark(D25, pc, "heartbeat_gap")
        if pc != split_minor_px:
            err(pc, out_a + timedelta(minutes=2, seconds=rng.randint(0, 50)), f"connection refused: {px_addr}", D25)
    # 우연 묶음: 같은 공정, 2시간 안, 3대지만 오류 원인이 서로 다름
    for k, (pc, msg) in enumerate(zip(sc["cluster_coincidental"],
                                      [STD_MESSAGES["java.lang.OutOfMemoryError"], STD_MESSAGES["bind failed"],
                                       STD_MESSAGES["heartbeat send failed"]])):
        err(pc, t(D25, 14, 5, 12) + timedelta(minutes=35 * k), msg, D25)

    # ---- 5. 아이템 ② 현재 상태 시나리오 (11-26 새벽에 멈춤) ----
    hidden = {}

    def stop_with(pc, at, cause, server_msg=None, client_msg=None, server_stop=True, **extra):
        plans[pc]["cut"] = at
        if server_msg:                                     # 서버에 닿을 수 있을 때만 서버 로그에 남는다
            plans[pc]["extra"].append((at, "ERROR", server_msg))
        elif server_stop:                                  # 누군가 서비스를 내렸다 올렸는데 다시 뜨지 못함
            plans[pc]["extra"].append((at, "STOP", ""))
        plans[pc]["client"].append((at + timedelta(seconds=30), "ERROR", client_msg or server_msg))
        plans[pc]["client"].append((at + timedelta(seconds=31), "INFO", "client stopped (fatal)"))
        hidden[pc] = {"cause": cause, "stopped_at": f"{at:%Y-%m-%d %H:%M:%S}",
                      "error_message": client_msg or server_msg, **extra}

    def at26():
        return t(D26, rng.randint(1, 6), rng.randint(0, 59), rng.randint(0, 59))

    bad_values = {"server_port": "84a3", "heartbeat_interval_sec": "abc", "retry_count": "-"}
    config_override = {}
    for pc, key in zip(sc["op_config"], bad_values):
        name = key_name(by_pc[pc]["client_version"], key)
        config_override[pc] = (name, bad_values[key])
        stop_with(pc, at26(), "config", client_msg=STD_MESSAGES["config parse failed"].format(key=name, value=bad_values[key]),
                  config_key=name, bad_value=bad_values[key], restart_succeeds_if="config_key_restored")
    for pc, p in zip(sc["op_transient"], ["heartbeat send failed", "license check timeout", "connection refused"]):
        stop_with(pc, at26(), "transient", server_msg=STD_MESSAGES[p], restart_succeeds_if="always")
    stop_with(sc["op_server_down"][0], at26(), "server_down", client_msg=STD_MESSAGES["connection refused"],
              server_stop=False, restart_succeeds_if="never")
    stop_with(sc["op_oom"][0], at26(), "oom", server_msg=STD_MESSAGES["java.lang.OutOfMemoryError"], restart_succeeds_if="never")
    stop_with(sc["op_bind"][0], at26(), "bind", server_msg=STD_MESSAGES["bind failed"], restart_succeeds_if="never")

    jvm_override = {}
    for pc in sc["op_proxy_missing"] + sc["op_proxy_uncomputable"] + sc["op_proxy_split_major"]:
        jvm_override[pc] = None
        cause = "proxy_uncomputable" if pc in sc["op_proxy_uncomputable"] else "proxy_missing"
        stop_with(pc, at26(), cause, client_msg=STD_MESSAGES["connection refused"],
                  expected_proxy=proxy_value(pc), restart_succeeds_if="jvm_proxy_correct")
    for pc in sc["op_proxy_wrong"]:
        wrong = f"192.168.{PROCESSES_INDEX[by_pc[pc]['process']]}.250:{PROXY_PORT}"
        jvm_override[pc] = wrong
        stop_with(pc, at26(), "proxy_wrong", client_msg=f"connection refused: {wrong}",
                  expected_proxy=proxy_value(pc), restart_succeeds_if="jvm_proxy_correct")
    # 프록시 PC 장애가 다시 나서 지속 중: 프록시와 그 프록시를 쓰는 PC가 모두 멈춘다.
    down_at = t(D26, 5, 0)
    plans[split_minor_px]["cut"] = down_at
    plans[split_minor_px]["client"].append((down_at, "ERROR", "java.lang.OutOfMemoryError: Java heap space"))
    plans[split_minor_px]["client"].append((down_at + timedelta(seconds=1), "INFO", "client stopped (fatal)"))
    hidden[split_minor_px] = {"cause": "oom", "stopped_at": f"{down_at:%Y-%m-%d %H:%M:%S}", "is_proxy": True,
                              "error_message": "java.lang.OutOfMemoryError: Java heap space", "restart_succeeds_if": "never"}
    for pc in split_minor:
        at = down_at + timedelta(minutes=2, seconds=rng.randint(0, 50))
        plans[pc]["cut"] = at
        plans[pc]["client"].append((at, "ERROR", f"connection refused: {px_addr}"))
        plans[pc]["client"].append((at + timedelta(seconds=1), "INFO", "client stopped (fatal)"))
        hidden[pc] = {"cause": "proxy_down", "stopped_at": f"{at:%Y-%m-%d %H:%M:%S}", "proxy_pc": split_minor_px,
                      "error_message": f"connection refused: {px_addr}", "expected_proxy": proxy_value(pc),
                      "restart_succeeds_if": "never"}
    # no_report PC: 일부는 실제로 멈춤, 일부는 실행 중인데 로그만 빠짐
    for k, pc in enumerate(sc["no_report"]):
        if k < 3:
            plans[pc]["client"].append((t(D24, 23, 58, 0), "INFO", "client stopped"))
            hidden[pc] = {"cause": "not_running", "stopped_at": f"{D24} 23:58:00", "restart_succeeds_if": "always"}
        else:
            hidden[pc] = {"cause": "log_missing", "restart_succeeds_if": "always"}

    # ---- 6. 서버 실행 로그 ----
    conn_addr = {pc: real_ip[route[pc]] for pc in by_pc}
    conn_addr[sc["route_bypass"][0]] = real_ip[sc["route_bypass"][0]]       # 프록시를 거치지 않음
    conn_addr[sc["route_unknown_source"][0]] = "10.99.0.7"                    # 알 수 없는 경로
    for o in orphans:
        conn_addr[o["pc_id"]] = o["ip"]
    n_slots = int((END - START) / SLOT)
    lines = []
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
            text = f"{ts:%Y-%m-%d %H:%M:%S} | {pc} | {ev} | {ver} | {ip} | {conn_addr[pc]}" + (f" | {msg}" if msg else "")
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

    # ---- 7. 가상 PC 폴더 ----
    pc_root = material / "pc"
    if pc_root.exists():
        shutil.rmtree(pc_root)
    for p in pcs:
        pc, ver = p["pc_id"], p["client_version"]
        d = pc_root / pc
        (d / "backup").mkdir(parents=True)
        conf = {key_name(ver, k): v for k, v in CONFIG_KEYS}
        if pc in config_override:
            name, value = config_override[pc]
            conf[name] = value
        (d / "client.properties").write_text(f"# ABZ client {ver}\n" + "".join(f"{k}={v}\n" for k, v in conf.items()),
                                             encoding="utf-8")
        args = ["-Xms256m", "-Xmx1024m"]
        if pc in proxy_of or pc in proxy_pcs and by_pc[pc]["internal_ip"]:
            value = jvm_override.get(pc, proxy_value(pc) if pc in proxy_of else f"{p['phys_internal']}:{PROXY_PORT}")
            if value:
                host, port = value.rsplit(":", 1)
                args += [f"-Dabz.proxy.host={host}", f"-Dabz.proxy.port={port}"]
        args += ["-jar", "abz-client.jar"]
        (d / "jvm.args").write_text(" ".join(args) + "\n", encoding="utf-8")
        h = hidden.get(pc, {})
        stopped = "stopped_at" in h
        (d / "service.json").write_text(json.dumps(
            {"status": "stopped" if stopped else "running",
             "since": h["stopped_at"] if stopped else "2026-11-20 08:00:00"}, ensure_ascii=False) + "\n", encoding="utf-8")
        clog = [(datetime(2026, 11, 20, 8, 0, 0), "INFO", f"client started version={ver}")]
        for ts, ev, msg in plans[pc]["extra"]:
            if ev == "START":
                clog.append((ts, "INFO", f"client started version={ver}"))
            elif ev == "STOP":
                clog.append((ts, "INFO", "client stopped"))
            elif ev == "ERROR":
                clog.append((ts, "ERROR", msg))
        clog += plans[pc]["client"]
        clog.sort(key=lambda x: x[0])
        (d / "client.log").write_text("".join(f"{ts:%Y-%m-%d %H:%M:%S} {lv} {m}\n" for ts, lv, m in clog
                                              if ts < END), encoding="utf-8")

    # ---- 8. 기준정보 DB (오입력 IP 반영) ----
    wrong_db_ip = {}
    for pc in sc["ip_mismatch"]:
        wrong_db_ip[pc] = {"db_ip": swap_digits(by_pc[pc]["ip"]), "log_ip": by_pc[pc]["ip"]}
        by_pc[pc]["ip"] = wrong_db_ip[pc]["db_ip"]
    db_dir = material / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "기준정보.sqlite"
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE pc_master (pc_id TEXT PRIMARY KEY, process TEXT NOT NULL, equipment_model TEXT NOT NULL,
                   ip TEXT NOT NULL, internal_ip TEXT, client_version TEXT NOT NULL, registered_at TEXT NOT NULL)""")
    con.executemany("""INSERT INTO pc_master VALUES (:pc_id,:process,:equipment_model,:ip,:internal_ip,
                       :client_version,:registered_at)""", pcs)
    con.commit()
    con.close()

    # ---- 9. 문서 자료 ----
    docs = material / "문서"
    docs.mkdir(parents=True, exist_ok=True)
    with open(docs / "오류메시지패턴표.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["message_pattern", "cause", "recommended_action", "auto_action"])
        w.writerows(PATTERNS)
    with open(docs / "설정기본값표.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["version", "key", "default", "allowed"])
        for ver in VERSIONS:
            for k, v in CONFIG_KEYS:
                w.writerow([ver, key_name(ver, k), v, "yes"])

    # ---- 10. 정답 자료 ----
    rec = root / "기록"
    rec.mkdir(parents=True, exist_ok=True)
    seen = {}
    for _, _, text in lines:
        parts = text.split(" | ")
        if parts[2] == "ERROR" and parts[0] < "2026-11-26" and not parts[6].startswith(tuple(p for p, *_ in PATTERNS)):
            seen[parts[6]] = VARIANT_MESSAGES.get(parts[6], "NEW")
    answer = {
        "seed": SEED, "report_date": D25, "total_pcs_in_db": len(pcs),
        "anomalies": {d: {pc: sorted(c) for pc, c in sorted(m.items())} for d, m in cats.items()},
        "scenarios": {k: sorted(v) for k, v in sc.items() if not k.startswith("op_")},
        "orphans": [o["pc_id"] for o in orphans],
        "ip_mismatch": wrong_db_ip,
        "route_mismatch": {"bypass": sc["route_bypass"], "unknown_source": sc["route_unknown_source"]},
        "error_classification": dict(sorted(seen.items())),
        "clusters": {
            "true": [
                {"pcs": sorted(sc["cluster_license"]), "pattern": "license check timeout", "shared": "client_version=2.3.1"},
                {"pcs": sorted(split_minor), "pattern": "connection refused", "shared": "proxy_pc=" + split_minor_px,
                 "proxy_pc": split_minor_px},
            ],
            "coincidental": [{"pcs": sorted(sc["cluster_coincidental"]), "shared": "process=TEST"}],
        },
        "proxy": {"port": PROXY_PORT, "combos": combos},
        "rules": {"heartbeat_gap_minutes": 30, "restart_loop_start_count": 5,
                  "gap_belongs_to_date_of_earlier_heartbeat": True,
                  "pattern_match": "메시지가 message_pattern으로 시작하면(대소문자 구분) 일치",
                  "no_report": "DB에 있는 PC인데 해당 날짜 로그가 한 줄도 없음(전날에는 있었음)"},
    }
    (rec / "정답표_일일리포트.json").write_text(json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8")
    op_answer = {"note": "아이템 ② 채점과 가상 재시작 전용. 에이전트 Tool로 읽지 않는다.",
                 "scenarios": {k: sorted(v) for k, v in sc.items() if k.startswith("op_")},
                 "pcs": dict(sorted(hidden.items()))}
    (rec / "정답_PC원인.json").write_text(json.dumps(op_answer, ensure_ascii=False, indent=2), encoding="utf-8")
    return answer


PROCESSES_INDEX = {proc: i for i, proc in enumerate(PROCESSES.values())}

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    a = generate()
    print("PC", a["total_pcs_in_db"], "| 2026-11-25 이상 PC", len(a["anomalies"][D25]),
          "| 미등록", a["orphans"], "| IP 오입력", len(a["ip_mismatch"]),
          "| 설비망 조합", list(a["proxy"]["combos"]))
