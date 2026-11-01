from java_coding_agent.domain.models import FeatureRun, PullRequest, RunStage, TaskItem
from java_coding_agent.evaluation.metrics import Thresholds, compute_metrics, judge, verdict
from tests.unit.fakes import FakeLlm

CASE = {"id": "c", "requirement": "r", "expectations": {"must_mention": ["新增", "刪除"]}}


def _run(success=True):
    run = FeatureRun("r1", "r")
    run.spec_md = "## 驗收條件\n場景: 新增\n場景: 刪除\n"
    run.tasks = [TaskItem("T1", "a", done=True), TaskItem("T2", "b", done=success)]
    run.record(RunStage.IMPLEMENT, "[T1] RED 確認：測試如預期失敗")
    run.record(RunStage.IMPLEMENT, "[T2] 測試在尚無實作時即通過", level="warning")
    run.record(RunStage.IMPLEMENT, "[T2] 測試失敗，修正第 1 次", level="warning")
    if success:
        run.succeed(PullRequest("https://x/pull/1", 1, "f"))
    else:
        run.fail(RunStage.VERIFY, "boom")
    return run


def test_compute_metrics_success():
    m = compute_metrics(CASE, _run(), 12.5)
    assert m.succeeded and m.task_count == 2 and m.red_confirmed == 1 and m.fix_rounds == 1
    assert m.scenario_count == 2 and m.spec_mentions_all and m.pr_url.endswith("/pull/1")
    assert m.tdd_discipline == 0.5


def test_compute_metrics_failure_records_stage():
    m = compute_metrics(CASE, _run(success=False), 1.0)
    assert not m.succeeded and m.failed_stage == "verify" and m.pr_url is None


def test_judge_parses_json_and_tolerates_garbage():
    llm = FakeLlm(
        {"eval.judge": ['前言 {"requirement_coverage": 4, "hexagonal_design": 5, "clarity": 3, "comment": "好"} 後記']}
    )
    scores, comment = judge(llm, CASE, _run())
    assert scores == {"requirement_coverage": 4.0, "hexagonal_design": 5.0, "clarity": 3.0} and comment == "好"
    scores, comment = judge(FakeLlm(default="not json"), CASE, _run())
    assert scores == {} and comment == "not json"


def test_verdict_thresholds():
    good = compute_metrics(CASE, _run(), 1)
    good.judge_scores = {"a": 4.0}
    bad = compute_metrics(CASE, _run(success=False), 1)
    passed, summary = verdict([good, good, bad], Thresholds())
    assert passed and abs(summary["success_rate"] - 2 / 3) < 1e-9
    passed, _ = verdict([bad, bad, good], Thresholds())
    assert not passed
    assert verdict([], Thresholds()) == (False, {})
