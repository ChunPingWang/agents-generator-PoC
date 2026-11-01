"""評估指標：對一次 SDD Run 的產出計算確定性指標，另以 LLM-as-judge 評分。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from java_coding_agent.domain.models import FeatureRun, RunStage
from java_coding_agent.domain.ports import LlmPort

_SCENARIO = re.compile(r"^\s*(場景|Scenario)[:：]", re.M)


@dataclass
class RunMetrics:
    case_id: str
    succeeded: bool
    failed_stage: str | None
    task_count: int
    tasks_done: int
    red_confirmed: int
    """幾個 task 的測試在無實作時確實失敗（TDD RED）。"""
    fix_rounds: int
    scenario_count: int
    spec_mentions_all: bool
    pr_url: str | None
    duration_seconds: float
    judge_scores: dict[str, float] = field(default_factory=dict)
    judge_comment: str = ""

    @property
    def tdd_discipline(self) -> float:
        return self.red_confirmed / self.task_count if self.task_count else 0.0

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tdd_discipline"] = self.tdd_discipline
        return d


def compute_metrics(case: dict[str, Any], run: FeatureRun, duration_seconds: float) -> RunMetrics:
    expectations = case.get("expectations", {})
    msgs = [e.message for e in run.events]
    failed_stage = None
    if run.stage is RunStage.FAILED:
        failed_stage = next((e.data.get("failed_stage") for e in run.events if e.stage is RunStage.FAILED), "unknown")
    text = (run.spec_md + run.plan_md).lower()
    return RunMetrics(
        case_id=case["id"],
        succeeded=run.stage is RunStage.SUCCEEDED,
        failed_stage=failed_stage,
        task_count=len(run.tasks),
        tasks_done=sum(t.done for t in run.tasks),
        red_confirmed=sum(1 for m in msgs if "RED 確認" in m),
        fix_rounds=sum(1 for m in msgs if "修正第" in m),
        scenario_count=len(_SCENARIO.findall(run.spec_md)),
        spec_mentions_all=all(k.lower() in text for k in expectations.get("must_mention", [])),
        pr_url=run.pull_request.url if run.pull_request else None,
        duration_seconds=duration_seconds,
    )


JUDGE_PROMPT = """你是一位嚴格的資深 Java 架構師，請評分以下由 AI 產生的規格與設計文件。

業務需求：
{requirement}

spec.md：
{spec}

plan.md：
{plan}

請針對四個面向各給 1 到 5 分（5 為最佳），只輸出以下 JSON，不要有其他文字：
{{"requirement_coverage": <int>, "hexagonal_design": <int>, "testability": <int>, "clarity": <int>,
 "comment": "<一句話總評>"}}
"""


def judge(llm: LlmPort, case: dict[str, Any], run: FeatureRun) -> tuple[dict[str, float], str]:
    """LLM-as-judge：預期 JSON；解析失敗時回傳空分數並保留原文作為註解。"""
    import json

    prompt = JUDGE_PROMPT.format(requirement=case["requirement"], spec=run.spec_md[:12000], plan=run.plan_md[:12000])
    raw = llm.complete("你只輸出 JSON。", prompt, name="eval.judge", metadata={"case_id": case["id"], "run_id": run.id})
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return {}, raw.strip()[:500]
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}, raw.strip()[:500]
    scores = {k: float(v) for k, v in data.items() if isinstance(v, int | float)}
    return scores, str(data.get("comment", ""))


@dataclass(frozen=True)
class Thresholds:
    min_success_rate: float = 2 / 3
    min_tdd_discipline: float = 0.5
    min_judge_average: float = 3.0


def verdict(all_metrics: list[RunMetrics], thresholds: Thresholds) -> tuple[bool, dict[str, float]]:
    if not all_metrics:
        return False, {}
    success_rate = sum(m.succeeded for m in all_metrics) / len(all_metrics)
    tdd = sum(m.tdd_discipline for m in all_metrics) / len(all_metrics)
    judged = [sum(m.judge_scores.values()) / len(m.judge_scores) for m in all_metrics if m.judge_scores]
    judge_avg = sum(judged) / len(judged) if judged else 0.0
    summary = {"success_rate": success_rate, "tdd_discipline": tdd, "judge_average": judge_avg}
    passed = (
        success_rate >= thresholds.min_success_rate
        and tdd >= thresholds.min_tdd_discipline
        and (not judged or judge_avg >= thresholds.min_judge_average)
    )
    return passed, summary
