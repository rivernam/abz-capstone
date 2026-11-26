"""summarize_daily_logs: 리포트 날짜의 서버 실행 로그를 집계한다 (읽기 전용, LLM 없음).

무엇을 하는지: 기준정보와 3일치 로그(전날, 당일, 다음날)로 이상 PC, 공정별 정상 PC 비율, 전일 대비,
식별 실패, 패턴표에 없는 오류 메시지, 공통 원인 묶음 후보를 계산한다.
언제 쓰는지: 일일 리포트를 만들 때 맨 처음 한 번. 숫자와 목록은 모두 이 결과에서 가져온다.
한계: 서버 로그만 보므로 no_report가 "실행 안 됨"인지 "로그 누락"인지 구분하지 못한다.
사용: python tools/summarize_daily_logs.py 2026-11-25
"""
import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from abzlib import paths
from abzlib.master import load_master
from abzlib.patterns import load_patterns, match_pattern
from abzlib.serverlog import anomalies, read_logs, shift

TYPES = ["heartbeat_gap", "restart_loop", "error", "no_report"]
CLUSTER_WINDOW = timedelta(hours=2)
CLUSTER_MIN = 3
GROUP_ATTRS = ["process", "equipment_model", "client_version", "shared_intranet_ip", "facility_band"]


def _fmt(ts):
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _ratio(n, d):
    return round(100 * n / d, 1) if d else 0.0


def identify_fail_reason(master, conn, internal):
    """구현명세 §5 식별 실패 사유. 정확히 일치하는 근거만 쓴다."""
    if internal:
        same = [pc for pc, r in master.items() if r["internal_ip"] == internal]
        if same:
            return "intranet_ip_typo", same[0]
        if any(r["ip"] == conn for r in master.values()):
            return "facility_ip_typo", None
    return "not_in_master", None


def _attrs(master):
    ip_count = {}
    for r in master.values():
        ip_count[r["ip"]] = ip_count.get(r["ip"], 0) + 1
    out = {}
    for pc, r in master.items():
        out[pc] = {
            "process": r["process"], "equipment_model": r["equipment_model"],
            "client_version": r["client_version"],
            "shared_intranet_ip": r["ip"] if ip_count[r["ip"]] > 1 else None,   # 같은 프록시를 쓰는 그룹
            "facility_band": r["internal_ip"].rsplit(".", 1)[0] + ".x" if r["internal_ip"] else None,
        }
    return out


def cluster_candidates(signals, attrs):
    """signals: {신호 이름: {pc: 처음 발생 시각}}. 같은 신호 + 같은 속성 + 2시간 안 + 3대 이상."""
    found = {}
    for sig, pcs in signals.items():
        for attr in GROUP_ATTRS:
            groups = {}
            for pc, ts in pcs.items():
                v = attrs[pc][attr]
                if v is not None:
                    groups.setdefault(v, []).append((ts, pc))
            for v, items in groups.items():
                items.sort()
                best = []
                for i in range(len(items)):
                    win = [x for x in items[i:] if x[0] - items[i][0] <= CLUSTER_WINDOW]
                    if len(win) > len(best):
                        best = win
                if len(best) >= CLUSTER_MIN:
                    key = (sig, tuple(sorted(pc for _, pc in best)))
                    c = found.setdefault(key, {"signal": sig, "pcs": list(key[1]), "shared": {},
                                               "first": _fmt(best[0][0]), "last": _fmt(best[-1][0])})
                    c["shared"][attr] = v
    out = sorted(found.values(), key=lambda c: (-len(c["pcs"]), c["signal"], c["pcs"]))
    for i, c in enumerate(out, 1):
        c["id"] = f"C{i}"
    return out


def summarize(day, log_dir=None, db_path=None, pattern_path=None):
    master = load_master(db_path or paths.DB_PATH)
    patterns = load_patterns(pattern_path or paths.PATTERN_TABLE)
    prev = shift(day, -1)
    lines, found_days = read_logs(log_dir or paths.SERVER_LOG_DIR, [prev, day, shift(day, 1)])
    if day not in found_days:
        return {"is_error": True, "error": f"{day} 서버 로그가 없다.", "next": "날짜를 확인하거나 로그 수집 상태를 점검한다."}
    today = anomalies(master, lines, day)
    yesterday = anomalies(master, lines, prev) if prev in found_days else None
    attrs = _attrs(master)

    # 공정별 현황
    by_process = {}
    for pc, r in master.items():
        p = by_process.setdefault(r["process"], {"registered": 0, "anomalous": 0})
        p["registered"] += 1
        p["anomalous"] += pc in today
    for p in by_process.values():
        p["normal"] = p["registered"] - p["anomalous"]
        p["normal_ratio"] = _ratio(p["normal"], p["registered"])
    total = len(master)
    normal = total - len(today)

    # 이상 PC 목록
    rows = []
    unmatched = {}
    for pc in sorted(today):
        f = today[pc]
        errs = []
        for l in f.get("error", []):
            p = match_pattern(l.message, patterns)
            errs.append({"time": _fmt(l.ts), "message": l.message,
                         "pattern": p["message_pattern"] if p else None,
                         "cause": p["cause"] if p else None,
                         "recommended_action": p["recommended_action"] if p else None})
            if not p:
                unmatched.setdefault(l.message, []).append(pc)
        status = None
        if yesterday is not None:
            status = "continuing" if pc in yesterday else "new"
        rows.append({
            "pc_id": pc, **{k: master[pc][k] for k in ("process", "equipment_model", "client_version")},
            "types": [t for t in TYPES if t in f],
            "gaps": [[_fmt(a), _fmt(b)] for a, b in f.get("heartbeat_gap", [])],
            "start_count": len(f.get("restart_loop", [])),
            "errors": errs,
            "vs_prev_day": status,
        })

    # 전일 대비
    vs = None
    if yesterday is not None:
        vs = {
            "prev_day": prev,
            "anomalous": {"today": len(today), "prev": len(yesterday)},
            "by_type": {t: {"today": sum(t in f for f in today.values()),
                            "prev": sum(t in f for f in yesterday.values())} for t in TYPES},
            "new": sorted(set(today) - set(yesterday)),
            "continuing": sorted(set(today) & set(yesterday)),
            "resolved": sorted(set(yesterday) - set(today)),
        }

    # 식별 실패
    fails = {}
    for l in lines:
        if l.event == "IDENTIFY_FAIL" and l.day == day:
            fails.setdefault((l.conn, l.internal), []).append(_fmt(l.ts))
    identify = []
    for (conn, internal), times in sorted(fails.items(), key=lambda x: x[0][0]):
        reason, pc = identify_fail_reason(master, conn, internal)
        identify.append({"conn": conn, "internal": internal, "attempts": len(times), "times": times,
                         "reason": reason, "pc_id": pc})

    # 묶음 후보 신호: 이상 유형별, 오류 패턴별(패턴표에 없는 메시지는 원문)
    signals = {}
    for pc, f in today.items():
        if "heartbeat_gap" in f:
            signals.setdefault("type:heartbeat_gap", {})[pc] = f["heartbeat_gap"][0][0]
        if "restart_loop" in f:
            signals.setdefault("type:restart_loop", {})[pc] = f["restart_loop"][0]
        for l in f.get("error", []):
            signals.setdefault("type:error", {}).setdefault(pc, l.ts)
            p = match_pattern(l.message, patterns)
            sig = "pattern:" + (p["message_pattern"] if p else l.message)
            signals.setdefault(sig, {}).setdefault(pc, l.ts)

    return {
        "report_date": day, "log_days_read": found_days,
        "totals": {"registered": total, "anomalous": len(today), "normal": normal,
                   "normal_ratio": _ratio(normal, total)},
        "by_process": dict(sorted(by_process.items())),
        "anomalies": rows,
        "vs_prev_day": vs,
        "needs_check": [r["pc_id"] for r in rows if "no_report" in r["types"]],
        "identify_fail": identify,
        "unmatched_messages": [{"message": m, "pcs": sorted(set(p))} for m, p in sorted(unmatched.items())],
        "cluster_candidates": cluster_candidates(signals, attrs),
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        print("usage: python tools/summarize_daily_logs.py YYYY-MM-DD")
        sys.exit(1)
    print(json.dumps(summarize(sys.argv[1]), ensure_ascii=False, indent=2))
