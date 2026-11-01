"""評估 runner：`java-coding-agent-eval [--cases FILE] [--ids a,b] [--no-judge] [--no-langfuse]`

對黃金資料集每一筆執行完整 SDD 流程（真實 LLM、真實 Gradle、真實 GitHub），計算指標並寫入 Langfuse Datasets / Scores。
結果另存為 evals/results/<timestamp>.json。以門檻判定，不以精確比對。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from java_coding_agent.config import AppConfig
from java_coding_agent.domain.models import FeatureRun
from java_coding_agent.evaluation.metrics import RunMetrics, Thresholds, compute_metrics, judge, verdict
from java_coding_agent.main import build_container, new_id

log = logging.getLogger("eval")


def load_cases(path: Path, ids: set[str] | None) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [c for c in cases if not ids or c["id"] in ids]


def push_to_langfuse(
    client: Any, dataset_name: str, case: dict[str, Any], run: FeatureRun, metrics: RunMetrics
) -> None:
    """建立/更新 dataset item，並把指標以 score 掛在該 run 的 trace 名稱上。"""
    try:
        try:
            client.get_dataset(dataset_name)
        except Exception:
            client.create_dataset(name=dataset_name, description="Java Coding Agent 評估黃金資料集")
        client.create_dataset_item(
            dataset_name=dataset_name,
            id=case["id"],
            input={"requirement": case["requirement"]},
            expected_output=case.get("expectations", {}),
            metadata={"last_run_id": run.id},
        )
        for name, value in {
            "succeeded": float(metrics.succeeded),
            "tdd_discipline": metrics.tdd_discipline,
            "fix_rounds": float(metrics.fix_rounds),
            "scenario_count": float(metrics.scenario_count),
            **{f"judge_{k}": v for k, v in metrics.judge_scores.items()},
        }.items():
            client.create_score(
                name=name,
                value=value,
                trace_id=None,
                comment=f"run {run.id}",
                metadata={"run_id": run.id, "case_id": case["id"]},
            )
        client.flush()
    except Exception as exc:  # 評估結果寫入失敗不應中斷評估
        log.warning("寫入 Langfuse 失敗：%s", exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="執行 Java Coding Agent 評估")
    parser.add_argument("--cases", default="evals/dataset/cases.jsonl")
    parser.add_argument("--ids", default="", help="逗號分隔的 case id；留空為全部")
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--no-langfuse", action="store_true")
    parser.add_argument("--out", default="evals/results")
    args = parser.parse_args(argv)

    container = build_container(AppConfig())
    ids = {s.strip() for s in args.ids.split(",") if s.strip()} or None
    cases = load_cases(Path(args.cases), ids)
    if not cases:
        print("沒有符合的評估案例", file=sys.stderr)
        return 2

    results: list[RunMetrics] = []
    for case in cases:
        log.info("=== 評估 %s：%s", case["id"], case["requirement"])
        started = time.monotonic()
        run = container.runs.start(case["requirement"])
        for _ in container.runs.subscribe(run.id):
            pass  # 等待終態
        run = container.runs.get(run.id) or run
        metrics = compute_metrics(case, run, time.monotonic() - started)
        if not args.no_judge and run.spec_md:
            llm = container.runs._pipeline_factory(lambda r: None)._llm  # 與流程相同的 LLM 設定
            metrics.judge_scores, metrics.judge_comment = judge(llm, case, run)
        if not args.no_langfuse and container.tracing.enabled:
            push_to_langfuse(container.tracing.client, "java-coding-agent-golden", case, run, metrics)
        results.append(metrics)
        log.info("結果 %s：%s", case["id"], json.dumps(metrics.as_dict(), ensure_ascii=False))

    passed, summary = verdict(results, Thresholds())
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "passed": passed,
        "summary": summary,
        "results": [m.as_dict() for m in results],
        "run_id_prefix": new_id(),
    }
    out_file = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": passed, **summary, "report": str(out_file)}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
