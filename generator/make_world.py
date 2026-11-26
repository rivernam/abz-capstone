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
START = datetime(2026, 11, 20, 0, 0, 0)        # 리포트 날짜 11-21~11-25의 전날부터
END = datetime(2026, 11, 26, 8, 0, 0)          # 리포트 시각(오전 8시) 직전까지
SLOT = timedelta(minutes=10)                   # 정상 신호 간격
D21, D22, D23, D24, D25, D26 = (f"2026-11-{d}" for d in range(21, 27))
REPORT_DAYS = [D21, D22, D23, D24, D25]
SERVER_HOST, SERVER_PORT, PROXY_PORT = "10.10.0.5", 8443, 3128

# 설비망을 쓰는 (공정, 설비 모델) 조합과 프록시 구성 방식
#   self   : 프록시 PC 하나가 설비망 IP를 등록하고 자기 자신을 거친다.
#   direct : 프록시 PC가 사내망 IP만 등록하고 직접 접속한다(프록시 값을 계산할 수 없음).
#   split  : 예외 조합. 프록시 PC 세 대가 나눠 맡는다(두 번째는 5대, 세 번째는 2대만 맡음).
PROXY_COMBOS = {("ETCH", "ET-300"): "self", ("DEPO", "DP-300"): "direct", ("LITHO", "LT-700"): "split"}
SPLIT_MINOR, SPLIT_TINY = 5, 2
TYPO = 100                                     # 기준정보 오입력 재현: 마지막 옥텟에 100을 더한다(쓰지 않는 주소)

# (message_pattern, cause, recommended_action, auto_action)
PATTERNS = [
    ("connection refused", "서버 접속 거부(서버 주소, 방화벽, 프록시 문제)", "server_host와 프록시 인자 확인, 서버 상태 확인", "proxy_check_restart"),
    ("config parse failed", "클라이언트 설정 값 오류", "해당 설정 키를 기본값으로 복구하고 서비스 재시작", "config_reset_restart"),
    ("java.lang.OutOfMemoryError", "JVM 힙 메모리 부족", "java 실행 인자 -Xmx 상향 검토(운영자 확인)", "none"),
    ("license check timeout", "라이선스 서버 응답 지연", "라이선스 서버 상태 확인", "restart"),
    ("bind failed", "포트 충돌", "포트를 쓰는 다른 프로세스 확인", "none"),
    ("heartbeat send failed", "서버 통신 지연", "네트워크 상태 확인", "restart"),
    ("no network interface matches", "설비망 IP 패턴 설정 오류", "프록시 PC 설비망 대역에 맞게 internal_ip_pattern 수정 후 재시작", "proxy_check_restart"),
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
# 11-21 함정: 표기가 크게 다른 변형과, 패턴표 단어와 겹치지만 원인이 다른 메시지
VARIANT_MESSAGES_2 = {
    "Address already in use: bind": "bind failed",
    "config file parse error at line 12": "config parse failed",
    "failed to send heartbeat: read timed out": "heartbeat send failed",
}
NEW_MESSAGES_2 = ["license expired on 2026-11-20", "connection reset by peer", "config file not found: client.properties"]


def key_name(version, key):
    return key.replace("_", ".") if version == "2.3.1" else key


def match_pattern(message):
    """패턴표 매칭 규칙: 메시지가 message_pattern으로 시작하면(대소문자 구분) 그 패턴이다."""
    for p, *_ in PATTERNS:
        if message.startswith(p):
            return p
    return None


def typo(ip):
    """기준정보 오입력 재현: 마지막 옥텟을 쓰지 않는 값으로 바꾼다."""
    head, last = ip.rsplit(".", 1)
    return f"{head}.{int(last) + TYPO}"


def ip_pattern(ip):
    """설치 프로그램이 프록시 PC 설비망 IP로 만드는 대역 패턴."""
    return ip.rsplit(".", 1)[0] + ".[0-9]+"


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
        proxies = [members[0]] if kind != "split" else members[:3]
        for pc in members:
            by_pc[pc]["internal_ip"] = by_pc[pc]["phys_internal"]
        assign = {}
        if kind == "split":
            minor = set(members[3:3 + SPLIT_MINOR])
            tiny = set(members[3 + SPLIT_MINOR:3 + SPLIT_MINOR + SPLIT_TINY])
            for pc in members:
                if pc == proxies[1] or pc in minor:
                    assign[pc] = proxies[1]
                elif pc == proxies[2] or pc in tiny:
                    assign[pc] = proxies[2]
                else:
                    assign[pc] = proxies[0]
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
    # 서버는 접속 주소를 사내망 IP로 보므로, 프록시를 거치는 PC의 기준정보 사내망 IP는 프록시 PC의 사내망 IP다.
    for pc, px in proxy_of.items():
        by_pc[pc]["ip"] = real_ip[px]
    has_pattern = set(proxy_of)                            # 설치 때 프록시 설비망 IP를 입력한 PC

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
    split_major, split_minor_px, split_tiny_px = combos[split_combo]["proxies"]
    split_minor = sorted(pc for pc, px in proxy_of.items() if px == split_minor_px and pc != split_minor_px)
    split_tiny = sorted(pc for pc, px in proxy_of.items() if px == split_tiny_px and pc != split_tiny_px)

    for pc in split_minor + split_tiny:                    # 프록시 장애 묶음, 소규모 그룹은 따로 둔다
        if pc in pool:
            pool.remove(pc)
    sc = {
        # 제약이 있는 시나리오부터 고른다
        "cluster_license": take(4, lambda pc: direct(pc) and by_pc[pc]["client_version"] == "2.3.1"),
        "cluster_coincidental": take(3, lambda pc: direct(pc) and by_pc[pc]["process"] == "TEST"),
        "identify_intranet_typo": take(1, direct) + take(1, lambda pc: combo_of.get(pc) == self_combo),
        "identify_facility_typo": take(1, lambda pc: combo_of.get(pc) == direct_combo),
        "op_proxy_missing": take(2, lambda pc: combo_of.get(pc) == self_combo),
        "op_proxy_wrong": take(1, lambda pc: combo_of.get(pc) == self_combo),
        "op_proxy_missing_direct_proxy": take(1, lambda pc: combo_of.get(pc) == direct_combo),
        "op_proxy_split_major": take(1, lambda pc: proxy_of.get(pc) == split_major),
        "op_pattern_mismatch": take(1, lambda pc: combo_of.get(pc) == self_combo),
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
    sc["op_proxy_uncomputable"] = [split_tiny[0]]          # 같은 사내망 IP를 쓰는 정상 PC가 3대 미만
    # 기준정보에 없는 새 PC(설치만 되고 등록 안 됨). 클라이언트는 PC ID를 모르므로 IP만 남는다.
    orphans = [{"label": "UNREGISTERED-1", "ip": "10.22.9.99", "internal": None, "client_version": "2.4.0"}]

    # ---- 4. 사건 계획 ----
    cats = {d: {} for d in REPORT_DAYS}

    def mark(day, pc, cat):
        cats[day].setdefault(pc, set()).add(cat)

    plans = {pc: {"skip": [], "extra": [], "cut": None, "client": [], "fails": []} for pc in by_pc}

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

    # ---- 4-2. 리포트 날짜 11-21~11-24 시나리오(일일 리포트 시험 세트) ----
    clusters_by_day = {d: {"true": [], "coincidental": []} for d in REPORT_DAYS}
    for d in (D21, D22, D23):                              # 날마다 조금씩 생기는 일반 이상
        for pc in take(2):
            s = t(d, rng.randint(6, 17), 0)
            plans[pc]["skip"].append((s, s + timedelta(hours=rng.randint(1, 3))))
            mark(d, pc, "heartbeat_gap")
        pc = take(1)[0]
        base = t(d, rng.randint(8, 16), 5)
        for n in range(rng.randint(5, 7)):
            plans[pc]["extra"] += [(base + timedelta(minutes=9 * n), "STOP", ""),
                                   (base + timedelta(minutes=9 * n + 1), "START", "")]
        mark(d, pc, "restart_loop")
        for pc, p in zip(take(2), rng.sample(["java.lang.OutOfMemoryError", "license check timeout", "heartbeat send failed"], 2)):
            err(pc, t(d, rng.randint(6, 21), rng.randint(0, 59), rng.randint(0, 59)), STD_MESSAGES[p], d)

    # 11-21: 처음 보는 오류 메시지(어려운 변형과 단어가 겹치는 신규)
    odd2 = list(VARIANT_MESSAGES_2) + NEW_MESSAGES_2
    for k, (pc, msg) in enumerate(zip(take(len(odd2)), odd2)):
        err(pc, t(D21, 7 + 2 * k, rng.randint(0, 59), rng.randint(0, 59)), msg, D21)

    # 11-22: 진짜 묶음(같은 프록시 그룹의 heartbeat 전송 실패)과 우연 묶음(각 PC의 포트 충돌)
    hb_group = take(4, lambda pc: proxy_of.get(pc) == split_major and pc != split_major)
    for k, pc in enumerate(hb_group):
        err(pc, t(D22, 13, 5, rng.randint(0, 59)) + timedelta(minutes=10 * k), STD_MESSAGES["heartbeat send failed"], D22)
    clusters_by_day[D22]["true"].append({"pcs": sorted(hb_group), "pattern": "heartbeat send failed",
                                         "shared": f"shared_intranet_ip={real_ip[split_major]}"})
    bind_group = take(3, lambda pc: direct(pc) and by_pc[pc]["process"] == "CLEAN")
    for k, pc in enumerate(bind_group):
        err(pc, t(D22, 9, 10, 7) + timedelta(minutes=45 * k), STD_MESSAGES["bind failed"], D22)
    clusters_by_day[D22]["coincidental"].append({"pcs": sorted(bind_group), "shared": "process=CLEAN",
                                                 "why": "포트 충돌은 PC마다의 문제"})

    # 11-23: 자기 자신을 거치는 프록시 PC 장애. 같은 공정의 다른 PC 하나가 같은 시간대에 따로 끊긴다(미끼).
    self_px = combos[self_combo]["proxies"][0]
    self_members = sorted(pc for pc, px in proxy_of.items() if px == self_px and pc != self_px)
    o_a, o_b = t(D23, 10, 0), t(D23, 10, 50)
    self_addr = f"{by_pc[self_px]['phys_internal']}:{PROXY_PORT}"
    for pc in [self_px] + self_members:
        plans[pc]["skip"].append((o_a, o_b))
        mark(D23, pc, "heartbeat_gap")
    for pc in self_members:
        err(pc, o_a + timedelta(minutes=1, seconds=rng.randint(0, 200)), f"connection refused: {self_addr}", D23)
    decoy = take(1, lambda pc: direct(pc) and by_pc[pc]["process"] == "ETCH")[0]
    plans[decoy]["skip"].append((t(D23, 9, 55), t(D23, 11, 5)))
    mark(D23, decoy, "heartbeat_gap")
    clusters_by_day[D23]["true"].append({"pcs": self_members, "pattern": "connection refused",
                                         "shared": f"shared_intranet_ip={real_ip[self_px]}", "proxy_pc": self_px})
    sc["decoy_gap_d23"] = [decoy]

    # 11-24: 배포 후 같은 버전에서 같은 설정 오류
    cfg_group = take(4, lambda pc: direct(pc) and by_pc[pc]["client_version"] == "2.4.2")
    for k, pc in enumerate(cfg_group):
        err(pc, t(D24, 8, 10, rng.randint(0, 59)) + timedelta(minutes=12 * k),
            STD_MESSAGES["config parse failed"].format(key="log_level", value="TRACE2"), D24)
    clusters_by_day[D24]["true"].append({"pcs": sorted(cfg_group), "pattern": "config parse failed",
                                         "shared": "client_version=2.4.2"})

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
    for pc in (sc["op_proxy_missing"] + sc["op_proxy_missing_direct_proxy"] + sc["op_proxy_split_major"]
               + sc["op_proxy_uncomputable"]):
        jvm_override[pc] = None
        cause = "proxy_uncomputable" if pc in sc["op_proxy_uncomputable"] else "proxy_missing"
        stop_with(pc, at26(), cause, client_msg=STD_MESSAGES["connection refused"],
                  expected_proxy=proxy_value(pc), restart_succeeds_if="jvm_proxy_correct")
    for pc in sc["op_proxy_wrong"]:
        wrong = f"192.168.{PROCESSES_INDEX[by_pc[pc]['process']]}.250:{PROXY_PORT}"
        jvm_override[pc] = wrong
        stop_with(pc, at26(), "proxy_wrong", client_msg=f"connection refused: {wrong}",
                  expected_proxy=proxy_value(pc), restart_succeeds_if="jvm_proxy_correct")
    pattern_override = {}
    for pc in sc["op_pattern_mismatch"]:
        bad = ip_pattern(f"192.168.9.1")
        pattern_override[pc] = bad
        name = key_name(by_pc[pc]["client_version"], "internal_ip_pattern")
        stop_with(pc, at26(), "pattern_mismatch", client_msg=f"no network interface matches {name}={bad}",
                  config_key=name, bad_value=bad, expected_pattern=ip_pattern(by_pc[proxy_of[pc]]["phys_internal"]),
                  expected_proxy=proxy_value(pc), restart_succeeds_if="pattern_correct")
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

    # 식별 실패: 11-24 저녁에 기준정보가 잘못 수정되었고, 23:30에 클라이언트를 재시작한 뒤로 식별에 실패한다.
    edit_at = t(D24, 23, 30)
    fail_tries = [edit_at + timedelta(minutes=1), t(D25, 8, 10, 4), t(D25, 13, 40, 31)]
    identify_fail = []
    fail_days = sorted({f"{x:%Y-%m-%d}" for x in fail_tries})
    for pc in sc["identify_intranet_typo"] + sc["identify_facility_typo"]:
        plans[pc]["cut"] = edit_at
        plans[pc]["extra"].append((edit_at, "STOP", ""))
        plans[pc]["fails"] = fail_tries
        plans[pc]["client"] += [(ts + timedelta(seconds=2), "ERROR", "identification failed: PC not found on server")
                                for ts in fail_tries]
        mark(D25, pc, "no_report")
        hidden[pc] = {"cause": "identify_fail", "stopped_at": f"{fail_tries[-1]:%Y-%m-%d %H:%M:%S}",
                      "error_message": "identification failed: PC not found on server", "restart_succeeds_if": "never"}
    facility_typo = {}
    intranet_typo = {}
    for pc in sc["identify_intranet_typo"]:
        intranet_typo[pc] = typo(by_pc[pc]["ip"])
    for pc in sc["identify_facility_typo"]:
        facility_typo[pc] = typo(by_pc[pc]["internal_ip"])

    clusters_by_day[D25]["true"] += [
        {"pcs": sorted(sc["cluster_license"]), "pattern": "license check timeout", "shared": "client_version=2.3.1"},
        {"pcs": sorted(split_minor), "pattern": "connection refused", "shared": "proxy_pc=" + split_minor_px,
         "proxy_pc": split_minor_px},
    ]
    clusters_by_day[D25]["coincidental"].append({"pcs": sorted(sc["cluster_coincidental"]), "shared": "process=TEST"})

    # ---- 5-2. 아이템 ② 개선 단계 시나리오: 표기 변형과 신규 원인 (11-26 새벽에 멈춤) ----
    # 기존 출력이 바뀌지 않게 별도 난수(SEED + 2)를 쓰고, 기존 take()가 모두 끝난 뒤의 pool에서 고른다.
    # 아무 사건도 없는 직접 접속 PC만 고르므로 11-25 이전 로그와 프록시 그룹 계산은 그대로다.
    # 서버 로그 jitter용 rng 호출 수는 정지 여부와 관계없이 슬롯마다 한 번이라 바뀌지 않는다.
    rng2 = random.Random(SEED + 2)
    # 각 공정 앞 25대(0101~0125)는 피한다: 기존 문의 시험 세트의 범위 문의(C0101~C0125)와 "실행 중 PC" 문의가 이 구간을 쓴다.
    quiet = [pc for pc in pool if int(pc[4:]) > 125 and direct(pc) and pc not in hidden and plans[pc]["cut"] is None
             and not any(plans[pc][k] for k in ("skip", "extra", "client", "fails"))]
    rng2.shuffle(quiet)

    def take2(n, pred=lambda pc: True):
        out = [pc for pc in quiet if pred(pc)][:n]
        if len(out) < n:
            raise RuntimeError("개선 단계 시나리오 PC가 모자란다")
        for pc in out:
            quiet.remove(pc)
            pool.remove(pc)
        return out

    def at26b():
        return t(D26, rng2.randint(1, 6), rng2.randint(0, 59), rng2.randint(0, 59))

    # 표기 변형(정답: 패턴표의 그 패턴). 재시작하면 복구되는 원인이다.
    variant_restart = [("Connection refused: connect", False), ("Connection refused: connect", False),
                       ("license server timeout (30000ms)", True), ("failed to send heartbeat: read timed out", True)]
    sc["op_variant"] = []
    for (msg, reached), pc in zip(variant_restart, take2(len(variant_restart))):
        stop_with(pc, at26b(), "variant_transient", server_msg=msg if reached else None, client_msg=None if reached else msg,
                  pattern={**VARIANT_MESSAGES, **VARIANT_MESSAGES_2}[msg], restart_succeeds_if="always")
        sc["op_variant"].append(pc)
    # 표기 변형 config: 메시지의 키가 기본값이 아니어서 기본값 복구가 필요하다(2.4.x 밑줄 표기 키).
    cfg_msg = "failed to parse config: retry_count=abc"
    pc = take2(1, lambda pc: by_pc[pc]["client_version"] != "2.3.1")[0]
    config_override[pc] = ("retry_count", "abc")
    stop_with(pc, at26b(), "variant_config", client_msg=cfg_msg, pattern=VARIANT_MESSAGES[cfg_msg],
              config_key="retry_count", bad_value="abc", restart_succeeds_if="config_key_restored")
    sc["op_variant"].append(pc)
    # 신규 원인: 패턴표 단어와 겹치지만 패턴표의 어느 것에도 대응하지 않는다. 재시작해도 다시 실패한다.
    sc["op_new_cause"] = []
    for (msg, reached), pc in zip([("license expired on 2026-11-20", True), ("connection reset by peer", False),
                                   ("config file not found: client.properties", False)], take2(3)):
        stop_with(pc, at26b(), "new_cause", server_msg=msg if reached else None, client_msg=None if reached else msg,
                  pattern=None, restart_succeeds_if="never")
        sc["op_new_cause"].append(pc)

    # ---- 6. 서버 실행 로그 ----
    conn_addr = {pc: real_ip[route[pc]] for pc in by_pc}
    reported_internal = {pc: (by_pc[pc]["phys_internal"] if pc in has_pattern else "-") for pc in by_pc}
    n_slots = int((END - START) / SLOT)
    lines = []

    def fail_line(ts, ver, conn, internal):
        return (ts, "~", f"{ts:%Y-%m-%d %H:%M:%S} | - | IDENTIFY_FAIL | {ver} | {conn} | {internal}")

    for p in pcs:
        pc, ver = p["pc_id"], p["client_version"]
        plan = plans[pc]
        for ts in plan["fails"]:
            lines.append(fail_line(ts, ver, conn_addr[pc], reported_internal[pc]))
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
            text = f"{ts:%Y-%m-%d %H:%M:%S} | {pc} | {ev} | {ver} | {conn_addr[pc]} | {reported_internal[pc]}" + \
                (f" | {msg}" if msg else "")
            lines.append((ts, pc, text))
    for o in orphans:                                      # 설치 직후부터 계속 식별 실패
        for ts in [t(D25, 9, 0, 12), t(D25, 9, 30, 40), t(D25, 13, 10, 5), t(D26, 7, 0, 9)]:
            lines.append(fail_line(ts, o["client_version"], o["ip"], o["internal"] or "-"))
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
        if pc in has_pattern:
            conf[key_name(ver, "internal_ip_pattern")] = pattern_override.get(
                pc, ip_pattern(by_pc[proxy_of[pc]]["phys_internal"]))
        if pc in config_override:
            name, value = config_override[pc]
            conf[name] = value
        (d / "client.properties").write_text(f"# ABZ client {ver}\n" + "".join(f"{k}={v}\n" for k, v in conf.items()),
                                             encoding="utf-8")
        args = ["-Xms256m", "-Xmx1024m"]
        if pc in proxy_of:
            value = jvm_override.get(pc, proxy_value(pc))
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
    for pc, v in intranet_typo.items():
        # 설비망 IP가 있으면 그것으로 기존 PC를 특정할 수 있지만, 없으면 미등록과 구별할 수 없다.
        expected = "intranet_ip_typo" if reported_internal[pc] != "-" else "not_in_master"
        identify_fail.append({"conn": conn_addr[pc], "internal": reported_internal[pc], "expected": expected,
                              "pc_id": pc, "db_ip": v, "days": fail_days})
        by_pc[pc]["ip"] = v
    for pc, v in facility_typo.items():
        identify_fail.append({"conn": conn_addr[pc], "internal": reported_internal[pc], "expected": "facility_ip_typo",
                              "pc_id": pc, "db_internal_ip": v, "days": fail_days})
        by_pc[pc]["internal_ip"] = v
    for o in orphans:
        identify_fail.append({"conn": o["ip"], "internal": o["internal"] or "-", "expected": "not_in_master", "pc_id": None,
                              "days": [D25, D26]})
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
            w.writerow([ver, key_name(ver, "internal_ip_pattern"), "", "computed"])   # 프록시 설비망 대역에서 계산

    # ---- 10. 정답 자료 ----
    rec = root / "기록"
    rec.mkdir(parents=True, exist_ok=True)
    seen = {}
    for _, _, text in lines:
        parts = text.split(" | ")
        if parts[2] == "ERROR" and parts[0] < D26 and not parts[6].startswith(tuple(p for p, *_ in PATTERNS)):
            seen[parts[6]] = {**VARIANT_MESSAGES, **VARIANT_MESSAGES_2}.get(parts[6], "NEW")
    answer = {
        "seed": SEED, "report_date": D25, "report_dates": REPORT_DAYS, "total_pcs_in_db": len(pcs),
        "anomalies": {d: {pc: sorted(c) for pc, c in sorted(m.items())} for d, m in cats.items()},
        "scenarios": {k: sorted(v) for k, v in sc.items() if not k.startswith("op_")},
        "identify_fail": identify_fail,
        "error_classification": dict(sorted(seen.items())),
        "clusters": clusters_by_day,
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
          "| 식별 실패", len(a["identify_fail"]),
          "| 설비망 조합", list(a["proxy"]["combos"]))
