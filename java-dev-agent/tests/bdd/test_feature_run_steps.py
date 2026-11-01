"""pytest-bdd 步驟：對應 specs/001-java-coding-agent/spec.md 的 Gherkin。"""

from __future__ import annotations

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from java_coding_agent.application.sdd_pipeline import PipelineOptions
from java_coding_agent.domain.models import FeatureRun, RepoSettings, RunStage
from tests.unit.fakes import FakeBuildRunner, FakeCodeRepository, InMemorySettings, scripted_llm

scenarios("feature_run.feature")


@pytest.fixture
def ctx():
    # 骨架 OK；T1 RED 失敗、GREEN 成功；T2 RED 失敗、GREEN 成功；verify 成功
    return {
        "repo": FakeCodeRepository(),
        "builder": FakeBuildRunner([True, False, True, False, True, True]),
        "settings": InMemorySettings(),
    }


@given(parsers.parse('目標 repo 已設定為 "{url}"'))
def repo_configured(ctx, url):
    ctx["settings"].save_repo_settings(RepoSettings(url=url))


@given("LLM 可用")
def llm_available(ctx):
    ctx["llm"] = scripted_llm(base_package="com.github.example.outcome")


@given("產出的程式碼在 Gradle 測試失敗且重試後仍失敗")
def build_always_fails(ctx):
    ctx["llm"] = scripted_llm(base_package="com.github.example.outcome")
    ctx["builder"] = FakeBuildRunner([True, False, False])


@when(parsers.parse('使用者輸入業務需求 "{requirement}"'))
def user_submits(ctx, make_pipeline, requirement):
    pipeline = make_pipeline(
        llm=ctx["llm"], builder=ctx["builder"], code_repo=ctx["repo"], options=PipelineOptions(max_fix_attempts=1)
    )
    pipeline._settings = ctx["settings"]
    ctx["run"] = pipeline.run(FeatureRun(id="bdd-run-1", requirement=requirement))


@then("Agent 產出 spec.md、plan.md、tasks.md")
def artifacts_exist(ctx):
    files = ctx["repo"].files
    slug = ctx["run"].feature_slug
    for name in ("spec.md", "plan.md", "tasks.md"):
        assert f"specs/001-{slug}/{name}" in files, name


@then("每個 task 都先有失敗測試再有通過實作")
def red_green(ctx):
    msgs = [e.message for e in ctx["run"].events]
    for task in ctx["run"].tasks:
        red = next(i for i, m in enumerate(msgs) if m.startswith(f"[{task.id}] RED 確認"))
        green = next(i for i, m in enumerate(msgs) if m.startswith(f"[{task.id}] GREEN 確認"))
        assert red < green


@then("Gradle 全部測試通過")
def all_tests_pass(ctx):
    assert any(e.message == "完整驗證通過" for e in ctx["run"].events)


@then("每個階段的 commit 都已推送到目標 repo 的 feature 分支")
def pushed_each_stage(ctx):
    run = ctx["run"]
    # 骨架、docs、每個 task、tasks 完成、publish 各推送一次以上
    assert len(ctx["repo"].pushed) >= 3 + len(run.tasks)
    assert set(ctx["repo"].pushed) == {run.branch}


@then("目標 repo 出現一個標題含功能名稱、已轉為 Ready for review 的 PR")
def pr_ready(ctx):
    run = ctx["run"]
    assert run.stage is RunStage.SUCCEEDED, run.error
    pr = ctx["repo"].prs[0]
    assert run.feature_slug in pr["title"] and not pr["title"].startswith("[WIP]")
    assert pr["draft"] is False and pr["repo"].full_name == "example/outcome"
    assert run.pull_request.draft is False


@then("PR 描述包含所有 tasks 的 checklist")
def pr_checklist(ctx):
    body = ctx["repo"].prs[0]["body"]
    for task in ctx["run"].tasks:
        assert f"- [x] {task.id}" in body


@then("PR 維持 Draft 狀態且不可審查合併")
def pr_stays_draft(ctx):
    pr = ctx["repo"].prs[0]
    assert pr["draft"] is True and pr["title"].startswith("[WIP]")
    assert not any(u["ready"] for u in pr["updates"])


@then("PR 描述與 Run 皆回報失敗階段與錯誤摘要")
def failure_reported(ctx):
    run = ctx["run"]
    assert run.stage is RunStage.FAILED
    assert run.error.startswith("[implement]") and "FAILED" in run.error
    body = ctx["repo"].prs[0]["body"]
    assert "失敗於 implement" in body and "FAILED" in body
