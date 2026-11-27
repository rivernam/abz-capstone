"""아이템 ② 판단 코드: PC별 조치 계획, 프록시 값 결정(ADR 0004), 식별 실패 근거, 회신 결과 표.

조치 대상과 쓰는 값은 모두 여기서 코드가 정한다. 모델은 이 결과를 읽고 쓰기 Tool을 고를 뿐이다.
"""
import re
from collections import Counter
from datetime import datetime, timedelta

from abzlib import history, source
from abzlib import virtual_pc as vpc
from abzlib.master import load_master
from abzlib.patterns import load_patterns, match_pattern
from abzlib.serverlog import read_logs

PROXY_MIN_PEERS = 3
SIMULTANEOUS = timedelta(minutes=30)       # 같은 그룹 connection refused를 "동시"로 보는 간격
WRITE_TOOLS = ("update_client_config", "update_jvm_proxy", "restart_client_service")

DONE, RUNNING, NOT_MET, ROLLED_BACK, RESTART_FAILED, OVER_LIMIT = (
    "조치함", "이미 실행 중이라 제외", "조건 미충족", "실패 후 원복", "재시작 실패", "상한 초과")
RESULT_TYPES = (DONE, RUNNING, NOT_MET, ROLLED_BACK, RESTART_FAILED, OVER_LIMIT)


class Context:
    """한 번의 Tool 호출 안에서 쓰는 읽기 전용 자료."""

    def __init__(self, world):
        self.world = world
        self.master = load_master(world.db_path)
        self.patterns = load_patterns(world.pattern_table)
        self.defaults = vpc.load_defaults(world)
        self._svc = {}
        self._literals = None

    def source_patterns(self, message):
        """메시지와 맞는 소스 리터럴의 패턴표 대응 집합(abzlib/source.patterns_for_message). 소스 색인은 한 번만 만든다."""
        if self._literals is None:
            self._literals = source.literal_index(self.world)
        return source.patterns_for_message(self.world, message, self._literals)

    def status(self, pc):
        if pc not in self._svc:
            self._svc[pc] = vpc.read_service(self.world, pc)
        return self._svc[pc]


def pattern_of(proxy_value):
    """프록시 설비망 IP에서 internal_ip_pattern을 계산한다(앞 세 자리 + [0-9]+)."""
    return proxy_value.split(":")[0].rsplit(".", 1)[0] + ".[0-9]+"


def compute_proxy(ctx, pc):
    """ADR 0004. {value 또는 None, reason, peers_running, candidates, suspect_proxy}."""
    w, m = ctx.world, ctx.master
    rec = m[pc]
    group = sorted(p for p, r in m.items() if p != pc and r["ip"] == rec["ip"] and r["internal_ip"])
    running = [p for p in group if ctx.status(p)["status"] == "running"]
    vals = {p: vpc.jvm_proxy(vpc.read_jvm(w, p)) for p in running}
    cand = dict(Counter(v or "(없음)" for v in vals.values()))
    out = {"value": None, "group_ip": rec["ip"], "peers_running": len(running), "candidates": cand, "suspect_proxy": None}
    if len(running) < PROXY_MIN_PEERS:
        out["reason"] = (f"같은 사내망 IP({rec['ip']})를 쓰는 실행 중 설비망 PC가 {len(running)}대라 "
                         f"{PROXY_MIN_PEERS}대 미만이다. 프록시 값을 정할 수 없다.")
        return out
    if len(set(vals.values())) != 1 or None in vals.values():
        out["reason"] = f"같은 사내망 IP를 쓰는 PC들의 프록시 값이 갈린다: {cand}"
        return out
    value = next(iter(vals.values()))
    same = [p for p, r in m.items() if p != pc and r["process"] == rec["process"]
            and r["equipment_model"] == rec["equipment_model"]]
    sv = [v for v in (vpc.jvm_proxy(vpc.read_jvm(w, p)) for p in same) if v]
    agree, differ = sum(v == value for v in sv), sum(v != value for v in sv)
    out["cross_check"] = {"agree": agree, "differ": differ}
    if differ > agree:
        out["reason"] = (f"교차 검증 실패: 같은 공정·설비 모델 PC {agree + differ}대 중 {differ}대가 "
                         f"{value}와 다른 값을 쓴다.")
        return out
    host = value.split(":")[0]
    suspect = next((p for p, r in m.items() if r["ip"] == rec["ip"] and r["internal_ip"] == host), None)
    out["suspect_proxy"] = suspect
    if suspect and ctx.status(suspect)["status"] != "running":
        out["reason"] = f"프록시 {suspect} 장애 의심(서비스 정지). 프록시 PC부터 확인한다."
        return out
    out["value"] = value
    out["reason"] = f"같은 사내망 IP를 쓰는 실행 중 설비망 PC {len(running)}대의 값이 모두 {value}로 같다."
    return out


def _since(svc):
    try:
        return datetime.strptime(svc.get("since", ""), vpc.TS)
    except ValueError:
        return None


def is_conn_refused(ctx, message):
    """connection refused 계열 오류인지. 패턴표 문구로 시작하거나, 표현만 다른 메시지라도 소스에서 메시지 전체와 맞는
    리터럴의 ErrorKind가 connection refused에 대응하면 그렇다고 본다(코드가 직접 확인, 모델 근거 없음)."""
    if not message:
        return False
    return message.startswith("connection refused") or "connection refused" in ctx.source_patterns(message)


def group_conn_refused(ctx, pc):
    """같은 사내망 IP 그룹에서 connection refused로 비슷한 시각(30분 안)에 멈춘 설비망 PC 목록(대상 포함).
    표현만 다른 접속 오류 메시지(예: "Connection refused: connect")로 멈춘 PC도 센다(is_conn_refused)."""
    w, m = ctx.world, ctx.master
    rec = m[pc]
    t0 = _since(ctx.status(pc))
    out = []
    for p, r in sorted(m.items()):
        if r["ip"] != rec["ip"] or not r["internal_ip"]:
            continue
        svc = ctx.status(p)
        t = _since(svc)
        err = vpc.last_error(w, p) if svc["status"] == "stopped" else None
        if err and is_conn_refused(ctx, err) and t and t0 and abs(t - t0) <= SIMULTANEOUS:
            out.append(p)
    return out


def identify_fail_hint(ctx, pc):
    """식별 실패 PC의 근거(구현명세 §5 규칙을 대상 PC 기준으로). 정확히 일치하는 근거만 쓴다."""
    rec = ctx.master[pc]
    lines, _ = read_logs(ctx.world.log_dir, [p.stem[7:] for p in ctx.world.log_dir.glob("server_*.txt")])
    pairs = sorted({(l.conn, l.internal) for l in lines if l.event == "IDENTIFY_FAIL"}, key=str)
    if rec["internal_ip"]:
        hit = [c for c, i in pairs if i == rec["internal_ip"]]
        if hit:
            return {"reason": "사내망 IP 오입력", "detail": f"서버가 설비망 IP {rec['internal_ip']}를 접속 주소 {hit[0]}에서 받았으나 "
                    f"기준정보 사내망 IP는 {rec['ip']}다. 기준정보 사내망 IP를 확인한다."}
        hit = [i for c, i in pairs if c == rec["ip"] and i]
        if hit:
            return {"reason": "설비망 IP 오입력 의심", "detail": f"같은 사내망 IP {rec['ip']}에서 설비망 IP {', '.join(hit)}로 식별에 "
                    f"실패했다. 기준정보 설비망 IP({rec['internal_ip']})를 확인한다."}
    return {"reason": "기준정보에서 근거를 찾지 못함", "detail": "미등록 또는 사내망 IP 오입력일 수 있다. 일일 리포트의 식별 실패 절과 "
            "PC의 실제 IP를 운영자가 확인한다."}


def plan_pc(ctx, pc, evidence=None):
    """PC 하나의 조치 계획. eligible이면 action에 할 일과 쓸 값이 들어 있다.

    evidence는 {pc_id: "소스경로:줄"}(선택). 오류 메시지가 패턴표와 정확히 일치하지 않을 때만 쓰고,
    코드가 그 줄을 직접 확인해(abzlib/source.verify_evidence) 통과해야 그 패턴으로 본다.
    """
    w = ctx.world
    r = {"pc_id": pc, "eligible": False, "action": None}
    rec = ctx.master.get(pc)
    if rec is None or vpc.pc_path(w, pc) is None:
        return {**r, "category": NOT_MET, "reason": "기준정보에 없는 PC(미등록)라 조치하지 않는다."}
    r.update({k: rec[k] for k in ("process", "equipment_model", "client_version", "ip", "internal_ip")})
    svc = ctx.status(pc)
    r["service"] = svc
    if svc["status"] == "running":
        return {**r, "category": RUNNING, "reason": "서비스가 실행 중이라 조치하지 않는다."}
    err = vpc.last_error(w, pc)
    r["error_message"] = err
    if err is None:
        return {**r, "category": NOT_MET, "reason": "서비스가 정지했지만 클라이언트 로그에 원인 오류가 없다. 운영자가 정지 경위를 확인한다."}
    if err.startswith("identification failed"):
        hint = identify_fail_hint(ctx, pc)
        return {**r, "category": NOT_MET, "identify_fail": hint,
                "reason": f"식별 실패(기준정보 문제)라 자동 조치하지 않는다. {hint['reason']}: {hint['detail']}"}
    pat = match_pattern(err, ctx.patterns)
    src = None
    if pat is None:
        loc = (evidence or {}).get(pc)
        if not loc:
            return {**r, "category": NOT_MET, "reason": "오류 메시지가 패턴표에 없어 자동 조치하지 않는다.",
                    "source_check": "표기만 다른 메시지일 수 있다. find_error_in_source로 오류 지점을 찾고, 근거를 error_evidence로 줄 수 있다."}
        v = source.verify_evidence(w, loc, err, ctx.patterns)
        r["source_evidence"] = {"ok": v["ok"], "location": loc, "reason": v["reason"], "error_kind": v.get("error_kind")}
        if not v["ok"]:
            return {**r, "category": NOT_MET, "reason": f"오류 메시지가 패턴표에 없고 소스 근거가 확인되지 않아 자동 조치하지 않는다({v['reason']})"}
        pat, src = v["pattern"], v
        # 2차 개선: 프록시를 거치는 PC라도 근거가 확인되면 표준 메시지와 같은 프록시 판단(동시 장애 확인, ADR 0004)을 받는다.
    r.update({"pattern": pat["message_pattern"], "cause": pat["cause"], "recommended_action": pat["recommended_action"],
              "auto_action": pat["auto_action"]})
    aa = pat["auto_action"]
    ver = rec["client_version"]
    props = vpc.read_props(w, pc)
    if aa == "none":
        return {**r, "category": NOT_MET, "reason": f"자동 조치하지 않는 오류다(운영자). 권장 조치: {pat['recommended_action']}"}
    if aa == "restart":
        return {**r, "eligible": True, "category": "조치 대상", "action": {"type": "restart_only"}}
    if aa == "config_reset_restart":
        if src:
            key, val = source.config_key_from(src["literal"], src["groups"])
            if key is None:
                return {**r, "category": NOT_MET, "reason": "소스 근거로 설정 오류임은 확인했지만 메시지에서 설정 키를 정할 수 없다."}
            dv0 = ctx.defaults.get((ver, key), (None,))[0]
            if props.get(key) not in (val, dv0):      # 기본값이면 이미 복구된 것(이 문의에서 고친 뒤 재시작 전)
                return {**r, "category": NOT_MET,
                        "reason": f"메시지의 값({key}={val})이 현재 설정 값({props.get(key)})과 달라 근거가 맞지 않는다."}
            m = True
        else:
            m = re.search(r"key=(\S+) value=(.*)$", err)
            key = m.group(1) if m else None
        dv = ctx.defaults.get((ver, key))
        if not m or dv is None or dv[1] != "yes":
            return {**r, "category": NOT_MET, "reason": f"설정 키 {key!r}가 버전 {ver}의 기본값 복구 허용 목록에 없다."}
        return {**r, "eligible": True, "category": "조치 대상",
                "action": {"type": "config_reset", "key": key, "value": dv[0], "current": props.get(key)}}
    if aa == "proxy_check_restart":
        if pat["message_pattern"] == "no network interface matches":
            if not rec["internal_ip"]:
                return {**r, "category": NOT_MET, "reason": "설비망 IP가 등록되지 않은 PC라 internal_ip_pattern을 계산할 수 없다."}
            px = compute_proxy(ctx, pc)
            r["proxy"] = px
            key = vpc.pattern_key(ctx.defaults, ver)
            if px["value"] is None or key is None:
                return {**r, "category": NOT_MET, "reason": px["reason"]}
            return {**r, "eligible": True, "category": "조치 대상",
                    "action": {"type": "pattern_fix", "key": key, "value": pattern_of(px["value"]), "current": props.get(key)}}
        if not rec["internal_ip"]:
            return {**r, "eligible": True, "category": "조치 대상", "action": {"type": "restart_only"}}
        together = group_conn_refused(ctx, pc)
        if len(together) >= 2:
            px_host = re.search(r"connection refused: (\d+\.\d+\.\d+\.\d+):", err)
            sus = next((p for p, x in ctx.master.items() if px_host and x["internal_ip"] == px_host.group(1)
                        and x["ip"] == rec["ip"]), None)
            return {**r, "category": NOT_MET, "together": together,
                    "reason": f"같은 사내망 IP 그룹에서 {len(together)}대({', '.join(together)})가 30분 안에 connection refused로 "
                              f"멈췄다. 프록시{(' ' + sus) if sus else ''} 장애 의심으로 운영자에게 넘긴다."}
        px = compute_proxy(ctx, pc)
        r["proxy"] = px
        if px["value"] is None:
            return {**r, "category": NOT_MET, "reason": px["reason"]}
        cur = vpc.jvm_proxy(vpc.read_jvm(w, pc))
        if cur == px["value"]:
            return {**r, "eligible": True, "category": "조치 대상", "action": {"type": "restart_only", "proxy_ok": cur}}
        return {**r, "eligible": True, "category": "조치 대상",
                "action": {"type": "jvm_proxy", "value": px["value"], "current": cur}}
    return {**r, "category": NOT_MET, "reason": f"알 수 없는 auto_action {aa!r}"}


ACTION_TOOL = {"config_reset": "update_client_config", "pattern_fix": "update_client_config", "jvm_proxy": "update_jvm_proxy"}


def result_table(world, post, parsed):
    """회신 표의 PC별 결과(6종). 조치 이력이 우선이고, 이력이 없는 PC는 현재 상태로 판단한다."""
    ctx = Context(world)
    pid = post["post_id"]
    rows = []
    mods = {}
    for h in history.rows(world, pid):
        if h["tool"] in ("update_client_config", "update_jvm_proxy") and h["result"] == "applied":
            mods.setdefault(h["pc_id"], []).append(h)
    for pc in parsed["targets"]:
        change = "; ".join(f"{k} {h['before'][k] or '(없음)'} → {h['after'][k]}" for h in mods.get(pc, []) for k in h["after"])
        rr = history.get(world, history.key(pid, "restart_client_service", pc))
        if rr:
            plan = rr["detail"].get("plan", {})
            if rr["result"] == "success":
                rows.append({"pc_id": pc, "result": DONE, "detail": (change + "; " if change else "") + "재시작 후 정상 실행 확인"})
            elif rr["result"] == "rolled_back":
                rows.append({"pc_id": pc, "result": ROLLED_BACK, "detail": f"{change}; 재시작 실패({rr['detail']['health']['reason']}) "
                             f"→ 수정 전 값으로 원복"})
            else:
                rows.append({"pc_id": pc, "result": RESTART_FAILED,
                             "detail": f"재시작 실패({rr['detail']['health']['reason']}). 권장 조치: {plan.get('recommended_action', '운영자 확인')}"})
            continue
        plan = plan_pc(ctx, pc)
        if mods.get(pc):
            rows.append({"pc_id": pc, "result": NOT_MET, "detail": f"{change} 후 재시작하지 않았다(처리 중단). 운영자가 재시작 여부를 정한다."})
        elif plan["category"] in (RUNNING, NOT_MET):
            rows.append({"pc_id": pc, "result": plan["category"], "detail": plan["reason"]})
        else:
            rows.append({"pc_id": pc, "result": NOT_MET, "detail": "조치 조건은 충족했지만 이번 처리에서 조치하지 않았다. 운영자가 확인한다."})
    for pc in parsed["over_limit"]:
        rows.append({"pc_id": pc, "result": OVER_LIMIT,
                     "detail": f"한 문의의 자동 조치 상한(20대)을 넘어 조치하지 않았다. 별도 문의로 다시 요청한다."})
    return rows


def reply_type(world, post_id):
    return "결과 회신" if any(h["tool"] == "restart_client_service" for h in history.rows(world, post_id)) else "방법 회신"
