"""아이템 ① 일일 리포트 파이프라인(고정 순서).

1. summarize_daily_logs로 집계한다(코드).
2. LLM 한 번 호출로 처음 보는 오류 메시지 분류, 묶음 판단, 요약 문장을 받는다.
3. 판단 결과를 검증하고 HTML을 만든다(코드).
4. publish_report로 발행한다(멱등).
5. 실행 기록을 기록/runs/에 남긴다.

사용: python -m runner.daily_report [--date YYYY-MM-DD] [--no-publish]
날짜를 빼면 전날로 실행한다.
"""
import argparse
import hashlib
import html
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from string import Template

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from abzlib import paths
from abzlib.patterns import load_patterns
from runner.llm import ClaudeJSON, LLMError, load_system_prompt
from tools.publish_report import publish_report
from tools.summarize_daily_logs import summarize

TYPE_LABEL = {"heartbeat_gap": "정상 신호 끊김", "restart_loop": "재시작 반복", "error": "오류", "no_report": "로그 없음"}
REASON_LABEL = {"intranet_ip_typo": "사내망 IP 오입력", "facility_ip_typo": "설비망 IP 오입력",
                "not_in_master": "기준정보에 없음 (미등록 또는 사내망 IP 오입력)"}
REASON_NEXT = {"intranet_ip_typo": "해당 PC의 기준정보 사내망 IP를 접속 주소로 고친다.",
               "facility_ip_typo": "같은 사내망 IP를 쓰는 PC 중 오늘 로그가 없는 PC의 설비망 IP를 확인한다.",
               "not_in_master": "새로 등록하기 전에, 오늘 로그가 없는 PC 중 사내망 IP가 비슷한 PC부터 확인한다."}
NEW_CAUSE = "새 오류 유형(패턴표에 없음)"


def llm_input(s, patterns):
    """LLM에 넘길 입력. 숫자는 facts에만 둔다."""
    vs = s["vs_prev_day"]
    facts = {
        "report_date": s["report_date"],
        "registered_pcs": s["totals"]["registered"], "anomalous_pcs": s["totals"]["anomalous"],
        "normal_pcs": s["totals"]["normal"], "normal_ratio_percent": s["totals"]["normal_ratio"],
        "by_process": s["by_process"],
        "anomaly_type_counts": {t: sum(t in r["types"] for r in s["anomalies"]) for t in TYPE_LABEL},
        "identify_fail_pairs": len(s["identify_fail"]),
        "needs_check_pcs": len(s["needs_check"]),
        "cluster_candidate_pc_counts": {c["id"]: len(c["pcs"]) for c in s["cluster_candidates"]},
    }
    if vs:
        facts["prev_day"] = {"date": vs["prev_day"], "anomalous_pcs": vs["anomalous"]["prev"],
                             "anomaly_type_counts": {t: v["prev"] for t, v in vs["by_type"].items()},
                             "new_pcs": len(vs["new"]), "continuing_pcs": len(vs["continuing"]),
                             "resolved_pcs": len(vs["resolved"])}
    rows = {r["pc_id"]: r for r in s["anomalies"]}
    cands = []
    for c in s["cluster_candidates"]:
        cands.append({"id": c["id"], "signal": c["signal"], "pc_count": len(c["pcs"]), "shared": c["shared"],
                      "first": c["first"], "last": c["last"],
                      "pcs": [{"pc_id": pc, "process": rows[pc]["process"], "equipment_model": rows[pc]["equipment_model"],
                               "client_version": rows[pc]["client_version"], "types": rows[pc]["types"],
                               "error_messages": sorted({e["message"] for e in rows[pc]["errors"]}),
                               "gaps": rows[pc]["gaps"]} for pc in c["pcs"]]})
    return {
        "facts": facts,
        "pattern_table": [{"message_pattern": p["message_pattern"], "cause": p["cause"]} for p in patterns],
        "unmatched_messages": [m["message"] for m in s["unmatched_messages"]],
        "cluster_candidates": cands,
        "identify_fail": [{"reason": f["reason"], "pc_id": f["pc_id"]} for f in s["identify_fail"]],
    }


def output_schema(patterns):
    return {
        "type": "object", "additionalProperties": False,
        "required": ["message_classes", "clusters", "summary"],
        "properties": {
            "message_classes": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["message", "matched_pattern", "reason"],
                "properties": {"message": {"type": "string"},
                               "matched_pattern": {"type": "string",
                                                   "enum": [p["message_pattern"] for p in patterns] + ["NEW"]},
                               "reason": {"type": "string"}}}},
            "clusters": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["id", "meaningful", "cause", "proxy_pc"],
                "properties": {"id": {"type": "string"}, "meaningful": {"type": "boolean"}, "cause": {"type": "string"},
                               "proxy_pc": {"type": "string"}}}},
            "summary": {"type": "string"},
        },
    }


def _numbers(obj):
    out = set()
    for tok in re.findall(r"\d+(?:\.\d+)?", json.dumps(obj, ensure_ascii=False)):
        out.add(tok)
        if "." in tok:
            out.add(tok.rstrip("0").rstrip("."))
    return out


def ungrounded_numbers(text, inp):
    """요약 문장에서 근거 없는 숫자를 찾는다.

    순수한 숫자(40, 86.7)는 facts에 있어야 한다. 식별자(PC-A0101, 2.3.1, LT-700, IP, C4)는 입력 어딘가에 그대로 있어야 한다.
    """
    allowed = _numbers(inp["facts"])
    whole = json.dumps(inp, ensure_ascii=False)
    bad = []
    for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9.:\-_]*", text):
        tok = tok.rstrip(".:-")
        if not re.search(r"\d", tok):
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", tok):
            if tok not in allowed:
                bad.append(tok)
        elif tok not in whole:
            bad.append(tok)
    return bad


def apply_judgement(s, out, patterns):
    """LLM 판단을 검증해 집계 결과에 붙인다. 빠지거나 잘못된 항목은 고정 값으로 채운다."""
    by_pattern = {p["message_pattern"]: p for p in patterns}
    classes, issues = {}, []
    for c in out.get("message_classes", []):
        if c["message"] in classes:
            continue
        classes[c["message"]] = c
    for m in s["unmatched_messages"]:
        if m["message"] not in classes:
            issues.append(f"분류 누락: {m['message']}")
            classes[m["message"]] = {"message": m["message"], "matched_pattern": "NEW", "reason": "LLM 분류 누락"}
    for r in s["anomalies"]:
        for e in r["errors"]:
            if e["pattern"] is None:
                k = classes[e["message"]]["matched_pattern"]
                e["classified_as"] = k
                e["cause"] = by_pattern[k]["cause"] if k in by_pattern else NEW_CAUSE
                e["recommended_action"] = by_pattern[k]["recommended_action"] if k in by_pattern else "운영자 확인"
    ids = {c["id"] for c in s["cluster_candidates"]}
    judged = {}
    for c in out.get("clusters", []):
        if c["id"] in ids:
            judged[c["id"]] = c
        else:
            issues.append(f"없는 묶음 ID: {c['id']}")
    for c in s["cluster_candidates"]:
        j = judged.get(c["id"])
        if j is None:
            issues.append(f"묶음 판단 누락: {c['id']}")
        c["meaningful"] = bool(j and j["meaningful"])
        c["cause"] = j["cause"] if j else ""
        px = (j or {}).get("proxy_pc", "").strip()
        if px and px not in s["_all_pcs"]:
            issues.append(f"기준정보에 없는 프록시 PC: {px}")
            px = ""
        c["proxy_pc"] = px
    msg_classes = [{"message": m["message"], "pcs": m["pcs"], **{k: classes[m["message"]][k] for k in ("matched_pattern", "reason")}}
                   for m in s["unmatched_messages"]]
    return msg_classes, issues


def _esc(x):
    return html.escape(str(x))


def render_html(s, msg_classes, summary, generated_at):
    t = s["totals"]
    vs = s["vs_prev_day"]
    delta = ""
    if vs:
        d = vs["anomalous"]["today"] - vs["anomalous"]["prev"]
        delta = f"<em>전일 {vs['anomalous']['prev']}대 ({'+' if d >= 0 else ''}{d})</em>"
    kpis = (f'<div class="kpi"><span>등록 PC</span><b>{t["registered"]}</b><em>기준정보 기준</em></div>'
            f'<div class="kpi"><span>정상 PC 비율</span><b>{t["normal_ratio"]}%</b><em>정상 PC {t["normal"]} / {t["registered"]}대</em></div>'
            f'<div class="kpi"><span>이상 PC</span><b>{t["anomalous"]}</b>{delta}</div>'
            f'<div class="kpi"><span>식별 실패</span><b>{len(s["identify_fail"])}</b><em>IP 쌍 기준</em></div>')
    bars = "".join(
        f'<div class="bar"><b>{_esc(p)}</b><div class="track"><div class="fill" style="width:{v["normal_ratio"]}%"></div></div>'
        f'<span class="n">{v["normal_ratio"]}% · 정상 {v["normal"]} / {v["registered"]}대</span></div>'
        for p, v in s["by_process"].items())
    summ = "".join(f"<p>{_esc(x.strip())}</p>" for x in re.split(r"(?<=[.다])\s+", summary) if x.strip())

    good = [c for c in s["cluster_candidates"] if c["meaningful"]]
    if good:
        clusters = "".join(
            f'<div class="cluster"><b>{_esc(c["cause"])}</b><br><span class="muted">신호 <code>{_esc(c["signal"])}</code> · '
            f'공유 속성 {_esc(", ".join(f"{k}={v}" for k, v in c["shared"].items()))} · {_esc(c["first"])} ~ {_esc(c["last"][11:])}</span>'
            + (f'<br>프록시 PC 의심: <b>{_esc(c["proxy_pc"])}</b>' if c["proxy_pc"] else "")
            + f'<br>{_esc(", ".join(c["pcs"]))}</div>' for c in good)
    else:
        clusters = '<p class="muted">의미 있는 묶음이 없다.</p>'
    if len(s["cluster_candidates"]) > len(good):
        clusters += f'<p class="muted">코드가 추린 후보 {len(s["cluster_candidates"])}개 중 {len(s["cluster_candidates"]) - len(good)}개는 우연이거나 중복으로 판단했다.</p>'

    rows = []
    for r in s["anomalies"]:
        tags = "".join(f'<span class="tag {t}">{TYPE_LABEL[t]}</span>' for t in r["types"])
        detail = []
        for g in r["gaps"]:
            detail.append(f"신호 끊김 {_esc(g[0][11:16])}~{_esc(g[1][11:16])}")
        if r["start_count"]:
            detail.append(f"START {r['start_count']}회")
        for e in r["errors"]:
            detail.append(f"<code>{_esc(e['message'])}</code>")
        causes = sorted({e["cause"] for e in r["errors"] if e.get("cause")})
        actions = sorted({e["recommended_action"] for e in r["errors"] if e.get("recommended_action")})
        st = {"new": '<span class="new">신규</span>', "continuing": '<span class="continuing">지속</span>'}.get(r["vs_prev_day"], "")
        rows.append(f"<tr><td><b>{_esc(r['pc_id'])}</b><br><span class=muted>{_esc(r['process'])} · {_esc(r['equipment_model'])} · {_esc(r['client_version'])}</span></td>"
                    f"<td>{tags}</td><td>{'<br>'.join(detail)}</td><td>{_esc(' / '.join(causes))}</td><td>{_esc(' / '.join(actions))}</td><td>{st}</td></tr>")
    table = ("<table><tr><th>PC</th><th>유형</th><th>근거</th><th>원인 후보</th><th>권장 조치</th><th>전일 대비</th></tr>"
             + "".join(rows) + "</table>") if rows else '<p class="muted">이상 PC가 없다.</p>'

    if msg_classes:
        msgs = "<table><tr><th>메시지</th><th>분류</th><th>근거</th><th>PC</th></tr>" + "".join(
            f"<tr><td><code>{_esc(m['message'])}</code></td><td>{'<b>새 오류 유형</b>' if m['matched_pattern'] == 'NEW' else _esc(m['matched_pattern'])}</td>"
            f"<td>{_esc(m['reason'])}</td><td>{_esc(', '.join(m['pcs']))}</td></tr>" for m in msg_classes) + "</table>"
    else:
        msgs = '<p class="muted">패턴표와 일치하지 않는 메시지가 없다.</p>'

    if s["identify_fail"]:
        ident = "<table><tr><th>접속 주소</th><th>설비망 IP</th><th>시도</th><th>사유</th><th>다음 할 일</th></tr>" + "".join(
            f"<tr><td>{_esc(f['conn'])}</td><td>{_esc(f['internal'] or '-')}</td><td>{f['attempts']}회</td>"
            f"<td>{_esc(REASON_LABEL[f['reason']])}{(' · ' + _esc(f['pc_id'])) if f['pc_id'] else ''}</td><td>{_esc(REASON_NEXT[f['reason']])}</td></tr>"
            for f in s["identify_fail"]) + "</table>"
    else:
        ident = '<p class="muted">식별 실패가 없다.</p>'
    needs = ", ".join(s["needs_check"]) if s["needs_check"] else "없음"
    resolved = ", ".join(vs["resolved"]) if vs and vs["resolved"] else "없음"

    tpl = Template((Path(__file__).parent / "templates" / "daily_report.html").read_text(encoding="utf-8"))
    return tpl.substitute(
        report_date=_esc(s["report_date"]), generated_at=_esc(generated_at), log_days=_esc(", ".join(s["log_days_read"])),
        kpis=kpis, summary=summ, process_bars=bars, clusters=clusters, anomaly_count=t["anomalous"],
        anomaly_table=table, message_table=msgs, identify_table=ident, needs_check=_esc(needs), resolved=_esc(resolved))


def run(day, llm=None, publish=True, log_dir=None, db_path=None, storage_dir=None, outbox=None, runs_dir=None):
    patterns = load_patterns(paths.PATTERN_TABLE)
    s = summarize(day, log_dir=log_dir, db_path=db_path)
    if s.get("is_error"):
        return s
    from abzlib.master import load_master
    s["_all_pcs"] = set(load_master(db_path or paths.DB_PATH))
    inp = llm_input(s, patterns)
    llm = llm or ClaudeJSON()
    system = load_system_prompt("daily-report")
    try:
        out, usage = llm.call(system, json.dumps(inp, ensure_ascii=False), output_schema(patterns))
    except LLMError as e:
        # LLM 없이도 숫자 리포트는 나가야 한다. 판단 칸은 비우고 사유를 남긴다.
        out, usage = {"message_classes": [], "clusters": [], "summary": f"LLM 판단을 받지 못해 요약을 생략했다({e})."}, {"error": str(e)}
    msg_classes, issues = apply_judgement(s, out, patterns)
    ungrounded = ungrounded_numbers(out["summary"], inp)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    page = render_html(s, msg_classes, out["summary"], generated_at)
    pub = publish_report(day, page, storage_dir=storage_dir, outbox=outbox) if publish else None
    record = {
        "report_date": day, "generated_at": generated_at, "usage": usage,
        "input_sha256": hashlib.sha256(json.dumps(inp, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        "llm_output": out, "issues": issues, "ungrounded_numbers": ungrounded,
        "message_classes": msg_classes,
        "clusters": [{k: c[k] for k in ("id", "signal", "pcs", "shared", "meaningful", "cause", "proxy_pc")} for c in s["cluster_candidates"]],
        "publish": pub,
    }
    runs_dir = Path(runs_dir or paths.RUNS_DIR)
    runs_dir.mkdir(parents=True, exist_ok=True)
    rec_path = runs_dir / f"daily_report_{day}_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
    rec_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"summary": s, "record": record, "record_path": str(rec_path), "html": page}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="일일 리포트를 만들고 발행한다.")
    ap.add_argument("--date", default=(date.today() - timedelta(days=1)).isoformat())
    ap.add_argument("--no-publish", action="store_true")
    a = ap.parse_args()
    r = run(a.date, publish=not a.no_publish)
    if r.get("is_error"):
        print(json.dumps(r, ensure_ascii=False))
        sys.exit(1)
    rec = r["record"]
    print(json.dumps({"record": r["record_path"], "publish": rec["publish"], "usage": rec["usage"],
                      "issues": rec["issues"], "ungrounded_numbers": rec["ungrounded_numbers"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
