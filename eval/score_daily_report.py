"""아이템 ① 채점(구현명세 §7).

1~3은 코드가 결정하는 부분(기준선), 4는 LLM 부분(개선 지표)이다.
사용: python -m eval.score_daily_report [--runs 3] [--date 2026-11-25]
결과: 기록/평가/daily_report_<시각>.json
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from abzlib import paths
from abzlib.patterns import load_patterns

NEW_CAUSE = "새 오류 유형(패턴표에 없음)"


def jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 0.0


def score(summary, record, answer, patterns):
    day = summary["report_date"]
    exp = {(pc, t) for pc, ts in answer["anomalies"][day].items() for t in ts}
    got = {(r["pc_id"], t) for r in summary["anomalies"] for t in r["types"]}
    tp = len(exp & got)
    trap = set(answer["scenarios"]["planned_restart_midnight"])
    ident_exp = {(f["conn"], f["internal"]): f["expected"] for f in answer["identify_fail"] if day in f["days"]}
    ident_got = {(f["conn"], f["internal"] or "-"): f["reason"] for f in summary["identify_fail"]}

    # LLM: 메시지 분류
    cls_all = answer["error_classification"]
    cls_got = {m["message"]: m["matched_pattern"] for m in record["message_classes"]}
    cls_exp = {m: cls_all[m] for m in cls_got if m in cls_all}
    cls_ok = sum(cls_got.get(m) == e for m, e in cls_exp.items())
    # LLM: 묶음
    good = [c for c in record["clusters"] if c["meaningful"]]
    truth = answer["clusters"][day]
    true_sets = [c["pcs"] for c in truth["true"]]
    detected = sum(any(jaccard(t, c["pcs"]) >= 0.5 for c in good) for t in true_sets)
    coincidental = sum(any(jaccard(t["pcs"], c["pcs"]) >= 0.5 for c in good) for t in truth["coincidental"])
    false_pos = sum(not any(jaccard(t, c["pcs"]) >= 0.5 for t in true_sets) for c in good)
    proxy_total = proxy_ok = 0
    for t in truth["true"]:
        if t.get("proxy_pc"):
            proxy_total += 1
            hits = [c for c in good if jaccard(t["pcs"], c["pcs"]) >= 0.5]
            proxy_ok += bool(hits) and all(c["proxy_pc"] == t["proxy_pc"] for c in hits)
    named_wrong = sum(bool(c["proxy_pc"]) and not any(t.get("proxy_pc") == c["proxy_pc"] for t in truth["true"]) for c in good)
    # 원인 후보: error PC의 원인이 패턴표 cause와 일치하는지
    by_pattern = {p["message_pattern"]: p["cause"] for p in patterns}
    cause_total = cause_ok = 0
    for r in summary["anomalies"]:
        for e in r["errors"]:
            key = e["pattern"] or cls_exp.get(e["message"])
            want = by_pattern.get(key, NEW_CAUSE)
            cause_total += 1
            cause_ok += e.get("cause") == want
    return {
        "baseline": {
            "anomaly_precision": round(tp / len(got), 3) if got else 1.0,
            "anomaly_recall": round(tp / len(exp), 3) if exp else 1.0,
            "trap_false_positives": len({pc for pc, _ in got} & trap),
            "identify_fail_reason_accuracy": round(sum(ident_got.get(k) == v for k, v in ident_exp.items()) / len(ident_exp), 3) if ident_exp else 1.0,
            "identify_fail_extra": len(set(ident_got) - set(ident_exp)),
            "registered_matches": summary["totals"]["registered"] == answer["total_pcs_in_db"],
        },
        "llm": {
            "message_class_accuracy": round(cls_ok / len(cls_exp), 3) if cls_exp else 1.0,
            "true_clusters": len(true_sets), "true_clusters_detected": detected,
            "coincidental_flagged": coincidental,
            "cluster_false_positives": false_pos,
            "proxy_clusters": proxy_total, "proxy_pc_correct": proxy_ok, "proxy_pc_wrongly_named": named_wrong,
            "message_classes": len(cls_exp), "message_classes_correct": cls_ok,
            "cause_match_rate": round(cause_ok / cause_total, 3) if cause_total else 1.0,
            "ungrounded_numbers": len(record["ungrounded_numbers"]),
            # 발행 전 검사(개선 3) 이전 기록에는 summary_guard가 없다. 그때는 첫 요약이 곧 발행된 요약이다.
            "ungrounded_first_draft": len((record.get("summary_guard") or {}).get("first_ungrounded", record["ungrounded_numbers"])),
            "summary_regenerated": int(bool((record.get("summary_guard") or {}).get("regenerated"))),
            "sentences_dropped": len((record.get("summary_guard") or {}).get("dropped_sentences", [])),
            "judgement_issues": len(record["issues"]),
        },
    }


def aggregate(day_scores):
    """여러 날짜 점수를 합친다. 비율은 건수로 다시 계산한다."""
    s = lambda k: sum(d["llm"][k] for d in day_scores)
    b = lambda k: [d["baseline"][k] for d in day_scores]
    return {
        "baseline": {"anomaly_precision_min": min(b("anomaly_precision")), "anomaly_recall_min": min(b("anomaly_recall")),
                     "trap_false_positives": sum(b("trap_false_positives")),
                     "identify_fail_reason_accuracy_min": min(b("identify_fail_reason_accuracy"))},
        "llm": {"message_class_accuracy": round(s("message_classes_correct") / max(1, s("message_classes")), 3),
                "true_cluster_recall": round(s("true_clusters_detected") / max(1, s("true_clusters")), 3),
                "coincidental_flagged": s("coincidental_flagged"), "cluster_false_positives": s("cluster_false_positives"),
                "proxy_pc_accuracy": round(s("proxy_pc_correct") / max(1, s("proxy_clusters")), 3),
                "proxy_pc_wrongly_named": s("proxy_pc_wrongly_named"),
                "cause_match_rate": round(sum(d["llm"]["cause_match_rate"] for d in day_scores) / len(day_scores), 3),
                "ungrounded_numbers": s("ungrounded_numbers"), "ungrounded_first_draft": s("ungrounded_first_draft"),
                "summary_regenerated": s("summary_regenerated"), "sentences_dropped": s("sentences_dropped"),
                "judgement_issues": s("judgement_issues")},
    }


def main():
    from runner.daily_report import run
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--label", default="baseline", help="이번 측정의 이름(예: baseline, v1-examples)")
    a = ap.parse_args()
    answer = json.loads((paths.RECORD_DIR / "정답표_일일리포트.json").read_text(encoding="utf-8"))
    patterns = load_patterns(paths.PATTERN_TABLE)
    out_dir = paths.RECORD_DIR / "평가"
    runs = []
    for i in range(a.runs):
        days = []
        for day in answer["report_dates"]:
            r = run(day, publish=False, runs_dir=out_dir / "runs")
            sc = score(r["summary"], r["record"], answer, patterns)
            sc.update(date=day, usage=r["record"]["usage"], record=r["record_path"])
            days.append(sc)
        agg = aggregate(days)
        runs.append({"aggregate": agg, "days": days})
        print(f"run {i + 1}:", json.dumps(agg["llm"], ensure_ascii=False))
    keys = runs[0]["aggregate"]["llm"].keys()
    mean = {k: round(sum(r["aggregate"]["llm"][k] for r in runs) / len(runs), 3) for k in keys}
    report = {"label": a.label, "runs": len(runs), "report_dates": answer["report_dates"],
              "baseline": runs[0]["aggregate"]["baseline"], "llm_mean": mean, "per_run": runs}
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"daily_report_{a.label}_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"baseline": report["baseline"], "llm_mean": mean, "saved": str(path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
