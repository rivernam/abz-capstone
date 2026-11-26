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
    day = answer["report_date"]
    exp = {(pc, t) for pc, ts in answer["anomalies"][day].items() for t in ts}
    got = {(r["pc_id"], t) for r in summary["anomalies"] for t in r["types"]}
    tp = len(exp & got)
    trap = set(answer["scenarios"]["planned_restart_midnight"])
    ident_exp = {(f["conn"], f["internal"]): f["expected"] for f in answer["identify_fail"]}
    ident_got = {(f["conn"], f["internal"] or "-"): f["reason"] for f in summary["identify_fail"]}

    # LLM: 메시지 분류
    cls_exp = answer["error_classification"]
    cls_got = {m["message"]: m["matched_pattern"] for m in record["message_classes"]}
    cls_ok = sum(cls_got.get(m) == e for m, e in cls_exp.items())
    # LLM: 묶음
    good = [c for c in record["clusters"] if c["meaningful"]]
    true_sets = [c["pcs"] for c in answer["clusters"]["true"]]
    detected = sum(any(jaccard(t, c["pcs"]) >= 0.5 for c in good) for t in true_sets)
    coincidental = sum(any(jaccard(t["pcs"], c["pcs"]) >= 0.5 for c in good) for t in answer["clusters"]["coincidental"])
    false_pos = sum(not any(jaccard(t, c["pcs"]) >= 0.5 for t in true_sets) for c in good)
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
            "identify_fail_reason_accuracy": round(sum(ident_got.get(k) == v for k, v in ident_exp.items()) / len(ident_exp), 3),
            "identify_fail_extra": len(set(ident_got) - set(ident_exp)),
            "registered_matches": summary["totals"]["registered"] == answer["total_pcs_in_db"],
        },
        "llm": {
            "message_class_accuracy": round(cls_ok / len(cls_exp), 3) if cls_exp else 1.0,
            "true_clusters_detected": f"{detected}/{len(true_sets)}",
            "coincidental_flagged": coincidental,
            "cluster_false_positives": false_pos,
            "cause_match_rate": round(cause_ok / cause_total, 3) if cause_total else 1.0,
            "ungrounded_numbers": len(record["ungrounded_numbers"]),
            "judgement_issues": len(record["issues"]),
        },
    }


def main():
    from runner.daily_report import run
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    answer = json.loads((paths.RECORD_DIR / "정답표_일일리포트.json").read_text(encoding="utf-8"))
    day = a.date or answer["report_date"]
    patterns = load_patterns(paths.PATTERN_TABLE)
    out_dir = paths.RECORD_DIR / "평가"
    results = []
    for i in range(a.runs):
        r = run(day, publish=False, runs_dir=out_dir / "runs")
        s = score(r["summary"], r["record"], answer, patterns)
        s["usage"] = r["record"]["usage"]
        s["record"] = r["record_path"]
        results.append(s)
        print(f"run {i + 1}:", json.dumps(s["llm"], ensure_ascii=False))
    llm_keys = [k for k, v in results[0]["llm"].items() if isinstance(v, (int, float))]
    mean = {k: round(sum(r["llm"][k] for r in results) / len(results), 3) for k in llm_keys}
    report = {"date": day, "runs": len(results), "baseline": results[0]["baseline"], "llm_mean": mean, "per_run": results}
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"daily_report_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"baseline": report["baseline"], "llm_mean": mean, "saved": str(path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
