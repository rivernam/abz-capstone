import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from abzlib.patterns import load_patterns
from eval.score_daily_report import jaccard, score
from generator.make_world import generate
from runner.daily_report import run, ungrounded_numbers, llm_input
from runner.llm import LLMError
from tools.publish_report import publish_report
from tools.summarize_daily_logs import summarize

DAY = "2026-11-25"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("w")
    ans = generate(root)
    m = root / "재료"
    return {"root": root, "ans": ans, "log": m / "서버로그", "db": m / "db" / "기준정보.sqlite",
            "patterns": load_patterns(m / "문서" / "오류메시지패턴표.csv")}


@pytest.fixture(scope="module")
def summary(world):
    return summarize(DAY, log_dir=world["log"], db_path=world["db"])


class PerfectLLM:
    """정답표대로 판단하는 가짜 LLM."""

    def __init__(self, ans, summary_text=None):
        self.ans, self.summary_text = ans, summary_text

    def call(self, system, user, schema):
        assert "일일 리포트" in system
        inp = json.loads(user)
        true_sets = [c["pcs"] for c in self.ans["clusters"]["true"]]
        clusters, used = [], set()
        for c in inp["cluster_candidates"]:
            pcs = [p["pc_id"] for p in c["pcs"]]
            hit = next((i for i, t in enumerate(true_sets) if jaccard(t, pcs) >= 0.5 and i not in used), None)
            if hit is not None:
                used.add(hit)
            clusters.append({"id": c["id"], "meaningful": hit is not None, "cause": "원인"})
        f = inp["facts"]
        return {"message_classes": [{"message": m, "matched_pattern": self.ans["error_classification"][m], "reason": "r"}
                                    for m in inp["unmatched_messages"]],
                "clusters": clusters,
                "summary": self.summary_text or f"정상 PC 비율은 {f['normal_ratio_percent']}%이고 이상 PC는 {f['anomalous_pcs']}대다."}, {"model": "fake"}


class BrokenLLM:
    def call(self, *a):
        raise LLMError("연결 실패")


def test_summary_matches_answer(world, summary):
    got = {r["pc_id"]: sorted(r["types"]) for r in summary["anomalies"]}
    assert got == world["ans"]["anomalies"][DAY]
    assert summary["totals"]["registered"] == 300
    assert sum(p["registered"] for p in summary["by_process"].values()) == 300
    assert {m["message"] for m in summary["unmatched_messages"]} == set(world["ans"]["error_classification"])
    reasons = {(f["conn"], f["internal"] or "-"): f["reason"] for f in summary["identify_fail"]}
    assert reasons == {(f["conn"], f["internal"]): f["expected"] for f in world["ans"]["identify_fail"]}


def test_true_clusters_are_candidates(world, summary):
    for t in world["ans"]["clusters"]["true"] + world["ans"]["clusters"]["coincidental"]:
        assert any(jaccard(t["pcs"], c["pcs"]) >= 0.5 for c in summary["cluster_candidates"]), t


def test_pipeline_with_perfect_llm(world, tmp_path):
    kw = dict(log_dir=world["log"], db_path=world["db"], storage_dir=tmp_path / "storage",
              outbox=tmp_path / "outbox.jsonl", runs_dir=tmp_path / "runs")
    r = run(DAY, llm=PerfectLLM(world["ans"]), **kw)
    rec = r["record"]
    assert rec["publish"]["published"] and not rec["issues"] and rec["ungrounded_numbers"] == []
    for text in ("정상 PC 비율", "공통 원인 묶음", "식별 실패", "새 오류 유형", "PC-"):
        assert text in r["html"]
    s = score(r["summary"], rec, world["ans"], world["patterns"])
    assert s["baseline"]["anomaly_precision"] == s["baseline"]["anomaly_recall"] == 1.0
    assert s["baseline"]["trap_false_positives"] == 0 and s["baseline"]["identify_fail_reason_accuracy"] == 1.0
    assert s["llm"]["message_class_accuracy"] == 1.0 and s["llm"]["true_clusters_detected"] == "2/2"
    assert s["llm"]["cluster_false_positives"] == 0 and s["llm"]["cause_match_rate"] == 1.0
    again = run(DAY, llm=PerfectLLM(world["ans"]), **kw)                     # 같은 날짜 재발행은 멱등
    assert again["record"]["publish"]["replayed"] is True
    assert len((tmp_path / "outbox.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_llm_text_is_escaped(world, tmp_path):
    r = run(DAY, llm=PerfectLLM(world["ans"], "<script>alert(1)</script>"), publish=False,
            log_dir=world["log"], db_path=world["db"], runs_dir=tmp_path)
    assert "<script>" not in r["html"] and "&lt;script&gt;" in r["html"]


def test_report_survives_llm_failure(world, tmp_path):
    r = run(DAY, llm=BrokenLLM(), publish=False, log_dir=world["log"], db_path=world["db"], runs_dir=tmp_path)
    assert "LLM 판단을 받지 못해" in r["html"] and "정상 PC 비율" in r["html"]
    assert r["record"]["issues"]                                             # 분류와 묶음 판단 누락이 기록된다


def test_ungrounded_numbers(world, summary):
    inp = llm_input(summary, world["patterns"])
    n = inp["facts"]["anomalous_pcs"]
    assert ungrounded_numbers(f"이상 PC {n}대, 버전 2.3.1", inp) == []
    assert ungrounded_numbers(f"이상 PC {n + 1}대, PC-Z9999", inp) == [str(n + 1), "PC-Z9999"]


def test_publish_report_validates(tmp_path):
    assert publish_report("25-11-2026", "<html></html>", storage_dir=tmp_path, outbox=tmp_path / "o")["is_error"]
    assert publish_report("2026-11-25", "", storage_dir=tmp_path, outbox=tmp_path / "o")["is_error"]


def test_missing_day_is_error(world):
    r = summarize("2026-01-01", log_dir=world["log"], db_path=world["db"])
    assert r["is_error"] and "next" in r
