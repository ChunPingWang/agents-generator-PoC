from java_coding_agent.application.sdd_pipeline import PipelineOptions
from java_coding_agent.domain.models import FeatureRun, RunStage
from tests.unit.fakes import FakeBuildRunner, FakeCodeRepository, scripted_llm


def new_run(req="開發一個 backend 服務，可以新增、修改、刪除、查詢的個人資料服務"):
    return FeatureRun(id="run-0001", requirement=req)


class TestHappyPath:
    def test_full_flow_opens_pr_with_checklist(self, make_pipeline):
        repo = FakeCodeRepository()
        # 骨架驗證 OK；T1 RED 失敗、GREEN 成功；T2 RED 失敗、GREEN 成功；verify 成功
        builder = FakeBuildRunner([True, False, True, False, True, True])
        llm = scripted_llm()
        events = []
        run = make_pipeline(llm=llm, builder=builder, code_repo=repo, events=events).run(new_run())

        assert run.stage is RunStage.SUCCEEDED, run.error
        assert run.feature_slug == "personal-profile-crud"
        assert run.pull_request.url.endswith("/pull/1")
        pr = repo.prs[0]
        assert "- [x] T1" in pr["body"] and "- [x] T2" in pr["body"]
        assert pr["title"].startswith("feat: personal-profile-crud") and pr["draft"] is False
        # 每個階段都推送同一條 feature 分支：骨架、docs、T1、T2、tasks 完成、publish
        assert set(repo.pushed) == {pr["branch"]} and len(repo.pushed) >= 6
        assert run.pull_request.draft is False
        assert all(t.done for t in run.tasks)
        # Draft PR 在 tasks 階段就已建立，之後每個 task 更新 checklist，最後轉為 ready
        updates = pr["updates"]
        assert updates[-1]["ready"] is True and any("- [x] T1" in (u["body"] or "") for u in updates[:-1])

    def test_sdd_artifacts_are_written_to_specs_dir(self, make_pipeline):
        repo = FakeCodeRepository()
        make_pipeline(code_repo=repo, builder=FakeBuildRunner([True, False, True])).run(new_run())
        assert "specs/001-personal-profile-crud/spec.md" in repo.files
        assert "specs/001-personal-profile-crud/plan.md" in repo.files
        assert "- [x] 完成" in repo.files["specs/001-personal-profile-crud/tasks.md"]

    def test_scaffold_only_created_once(self, make_pipeline):
        repo = FakeCodeRepository(existing={"settings.gradle.kts": "x", "specs/001-a/spec.md": "old"})
        run = make_pipeline(code_repo=repo, builder=FakeBuildRunner([False, True])).run(new_run())
        assert run.stage is RunStage.SUCCEEDED, run.error
        assert not any(c.startswith("chore: 建立") for c in repo.commits)
        assert any("已有骨架" in e.message for e in run.events)

    def test_stage_order_and_tdd_cycle_events(self, make_pipeline):
        run = make_pipeline(builder=FakeBuildRunner([True, False, True])).run(new_run())
        stages = [e.stage for e in run.events]
        order = [
            RunStage.SCAFFOLD,
            RunStage.SPECIFY,
            RunStage.PLAN,
            RunStage.TASKS,
            RunStage.IMPLEMENT,
            RunStage.VERIFY,
            RunStage.PUBLISH,
            RunStage.SUCCEEDED,
        ]
        idx = [stages.index(s) for s in order]
        assert idx == sorted(idx)
        msgs = " | ".join(e.message for e in run.events)
        assert "RED 確認" in msgs and "GREEN 確認" in msgs

    def test_llm_receives_editable_system_prompt(self, make_pipeline, prompt_store):
        prompt_store.save_new_version("# 自訂規則 " + "z" * 60, "custom")
        llm = scripted_llm()
        make_pipeline(llm=llm, builder=FakeBuildRunner([True, False, True])).run(new_run())
        assert all(c["system"].startswith("# 自訂規則") for c in llm.calls)

    def test_implement_prompt_includes_previous_files(self, make_pipeline):
        llm = scripted_llm()
        make_pipeline(llm=llm, builder=FakeBuildRunner([True, False, True])).run(new_run())
        second_task_test_call = llm.calls_named("sdd.write_test")[1]
        assert "Profile.java" in second_task_test_call["user"]  # T1 的實作在 T2 的 context 中


class TestFailures:
    def test_draft_pr_stays_draft_when_fix_attempts_exhausted(self, make_pipeline):
        repo = FakeCodeRepository()
        builder = FakeBuildRunner([True, False, False])  # 骨架 OK，之後永遠失敗
        run = make_pipeline(code_repo=repo, builder=builder, options=PipelineOptions(max_fix_attempts=2)).run(new_run())
        assert run.stage is RunStage.FAILED
        assert "[implement]" in run.error and "修正 2 次" in run.error
        pr = repo.prs[0]
        assert pr["draft"] is True and pr["title"].startswith("[WIP]")
        assert "失敗於 implement" in pr["body"] and "- [ ] T1" in pr["body"]
        assert repo.pushed and set(repo.pushed) == {run.branch}  # 失敗狀態也推送到分支
        assert any(e.data.get("failed_stage") == "implement" for e in run.events)
        fix_events = [e for e in run.events if "修正第" in e.message]
        assert fix_events and "FAILED" in fix_events[0].data["build_output"]  # 建置輸出保留於事件供診斷

    def test_strict_red_fails_when_tests_pass_without_impl(self, make_pipeline):
        run = make_pipeline(builder=FakeBuildRunner([True, True]), options=PipelineOptions(strict_red=True)).run(
            new_run()
        )
        assert run.stage is RunStage.FAILED
        assert "RED" in run.error

    def test_non_strict_red_only_warns(self, make_pipeline):
        run = make_pipeline(builder=FakeBuildRunner([True, True])).run(new_run())
        assert run.stage is RunStage.SUCCEEDED
        assert any(e.level == "warning" and "RED" in e.message for e in run.events)

    def test_scaffold_build_failure_stops_early(self, make_pipeline):
        llm = scripted_llm()
        run = make_pipeline(llm=llm, builder=FakeBuildRunner([False])).run(new_run())
        assert run.stage is RunStage.FAILED and "[scaffold]" in run.error
        assert llm.calls == []

    def test_unparseable_tasks_output_fails_after_retries(self, make_pipeline):
        llm = scripted_llm(**{"sdd.tasks": ["沒有任何檔案區塊"]})
        run = make_pipeline(llm=llm, builder=FakeBuildRunner([True])).run(new_run())
        assert run.stage is RunStage.FAILED and "[tasks]" in run.error
        assert len(llm.calls_named("sdd.tasks")) == 3  # 1 + max_parse_retries

    def test_parse_retry_feeds_back_error(self, make_pipeline):
        llm = scripted_llm(**{"sdd.specify": ["garbage", scripted_llm()._scripts["sdd.specify"][0]]})
        run = make_pipeline(llm=llm, builder=FakeBuildRunner([True, False, True])).run(new_run())
        assert run.stage is RunStage.SUCCEEDED, run.error
        assert "上一次輸出有問題" in llm.calls_named("sdd.specify")[1]["user"]

    def test_push_failure_marks_run_failed(self, make_pipeline):
        repo = FakeCodeRepository(fail_push=True)
        run = make_pipeline(code_repo=repo, builder=FakeBuildRunner([True, False, True])).run(new_run())
        # 第一次推送發生在骨架 commit 之後
        assert run.stage is RunStage.FAILED and "[scaffold]" in run.error and "push rejected" in run.error
        assert repo.prs == []

    def test_pr_body_llm_failure_falls_back_to_checklist(self, make_pipeline):
        class BoomError(Exception):
            pass

        llm = scripted_llm()
        original = llm.complete

        def complete(system, user, *, name="", metadata=None, effort="normal"):
            if name == "sdd.pr_body":
                raise BoomError("llm down")
            return original(system, user, name=name, metadata=metadata, effort=effort)

        llm.complete = complete  # type: ignore[method-assign]
        repo = FakeCodeRepository()
        run = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, True])).run(new_run())
        assert run.stage is RunStage.SUCCEEDED
        assert "- [x] T1" in repo.prs[0]["body"]


def test_fix_can_delete_files(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, FIX_OUTPUT, _fill

    repo = FakeCodeRepository()
    llm = scripted_llm(
        **{
            "sdd.fix": [
                # 刪除 plan.md（之後不會再被寫回），確認刪除確實作用在 workspace 上
                "=== DELETE FILE: specs/001-personal-profile-crud/plan.md ===\n"
                + _fill(FIX_OUTPUT, DEFAULT_BASE_PACKAGE)
            ]
        }
    )
    run = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, False, True])).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert "specs/001-personal-profile-crud/plan.md" not in repo.files
    assert any("刪除 1 個檔案" in e.message for e in run.events)


def test_wrong_package_output_is_rejected_and_retried(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, IMPL_OUTPUT, _fill

    wrong = (
        "=== FILE: domain/src/main/java/com/github/chunpingwang/ollamaagentoutcome/persistence/"
        "Profile.java ===\n"
        "package com.github.chunpingwang.ollamaagentoutcome.persistence;\n"
        "public record Profile(String name) {}\n=== END FILE ===\n"
    )
    llm = scripted_llm(**{"sdd.write_impl": [wrong, _fill(IMPL_OUTPUT, DEFAULT_BASE_PACKAGE)]})
    run = make_pipeline(llm=llm, builder=FakeBuildRunner([True, False, True])).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    second_call = llm.calls_named("sdd.write_impl")[1]["user"]
    assert (
        "套件與模組目錄不一致" in second_call
        and "com.github.chunpingwang.ollamaagentoutcome.persistence" in second_call
    )


def test_duplicate_type_names_are_rejected_with_feedback(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, IMPL_OUTPUT, _fill

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    dup = (
        f"=== FILE: application/src/main/java/{base_path}/application/port/in/DeleteProfilePort.java ===\n"
        f"package {DEFAULT_BASE_PACKAGE}.application.port.in;\n"
        "public interface DeleteProfilePort {}\n=== END FILE ===\n"
        f"=== FILE: application/src/main/java/{base_path}/application/port/out/DeleteProfilePort.java ===\n"
        f"package {DEFAULT_BASE_PACKAGE}.application.port.out;\n"
        "public interface DeleteProfilePort {}\n=== END FILE ===\n"
    )
    llm = scripted_llm(**{"sdd.write_impl": [dup, _fill(IMPL_OUTPUT, DEFAULT_BASE_PACKAGE)]})
    run = make_pipeline(llm=llm, builder=FakeBuildRunner([True, False, True])).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert "同名型別" in llm.calls_named("sdd.write_impl")[1]["user"]


def test_scaffold_files_are_protected_except_use_case_configuration(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, IMPL_OUTPUT, _fill

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    bad = (
        '=== FILE: settings.gradle.kts ===\nrootProject.name = "hacked"\n=== END FILE ===\n'
        f"=== FILE: bootstrap/src/test/java/{base_path}/ArchitectureTest.java ===\n"
        f"package {DEFAULT_BASE_PACKAGE}.bootstrap;\nclass ArchitectureTest {{}}\n=== END FILE ===\n"
    )
    llm = scripted_llm(**{"sdd.write_impl": [bad, _fill(IMPL_OUTPUT, DEFAULT_BASE_PACKAGE)]})
    repo = FakeCodeRepository()
    run = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, True])).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert repo.files["settings.gradle.kts"] != 'rootProject.name = "hacked"\n'
    assert "骨架檔案" in llm.calls_named("sdd.write_impl")[1]["user"]


def test_protected_scaffold_edits_are_dropped_when_other_files_remain(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, IMPL_OUTPUT, _fill

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    # 骨架假檔案的路徑是 bootstrap/src/test/java/<base>/ArchitectureTest.java（package 即 base）
    mixed = (
        f"=== FILE: bootstrap/src/test/java/{base_path}/ArchitectureTest.java ===\n"
        f"package {DEFAULT_BASE_PACKAGE};\nclass ArchitectureTest {{}}\n=== END FILE ===\n"
    ) + _fill(IMPL_OUTPUT, DEFAULT_BASE_PACKAGE)
    llm = scripted_llm(**{"sdd.write_impl": [mixed]})
    repo = FakeCodeRepository()
    run = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, True])).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, [e.message for e in run.events if "解析失敗" in e.message]
    assert repo.files[f"bootstrap/src/test/java/{base_path}/ArchitectureTest.java"] == "class A {}\n"
    assert f"domain/src/main/java/{base_path}/domain/Profile.java" in repo.files
    assert len(llm.calls_named("sdd.write_impl")) == 2  # T1、T2 各一次 GREEN，沒有因骨架檔被退回重試
    assert any("已忽略對骨架檔案的修改" in e.message for e in run.events)


def test_task_without_tests_skips_red_and_continues(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, IMPL_OUTPUT

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    interface_only = (
        f"=== FILE: application/src/main/java/{base_path}/application/port/in/X.java ===\n"
        f"package {DEFAULT_BASE_PACKAGE}.application.port.in;\npublic interface X {{}}\n"
    )
    # T1 的 RED 只產生介面；T2 正常（T2 的 tasks.md 測試提示指向 application 模組）
    app_test = (
        f"=== FILE: application/src/test/java/{base_path}/application/usecase/CreateProfileServiceTest.java ===\n"
        f"package {DEFAULT_BASE_PACKAGE}.application.usecase;\nclass CreateProfileServiceTest {{}}\n"
    )
    from tests.unit.fakes import _fill

    # GREEN 必須補上 X 的實作，否則會被「inbound port 尚無實作」檢查擋下
    impl_with_service = (
        f"=== FILE: application/src/main/java/{base_path}/application/usecase/XService.java ===\n"
        f"package {DEFAULT_BASE_PACKAGE}.application.usecase;\npublic class XService implements X {{}}\n"
        "=== END FILE ===\n"
    ) + _fill(IMPL_OUTPUT, DEFAULT_BASE_PACKAGE)
    llm = scripted_llm(
        **{"sdd.write_test": [interface_only, app_test], "sdd.write_impl": [impl_with_service, impl_with_service]}
    )
    repo = FakeCodeRepository()
    # 骨架 OK；T1 無 RED 執行，GREEN 成功；T2 RED 失敗、GREEN 成功；verify 成功
    run = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, True, False, True, True])).run(
        new_run()
    )
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert any("略過 RED" in e.message for e in run.events)
    assert (
        f"application/src/main/java/{DEFAULT_BASE_PACKAGE.replace('.', '/')}/application/port/in/X.java" in repo.files
    )
    assert "本任務無專屬測試" in llm.calls_named("sdd.write_impl")[0]["user"]
    _ = IMPL_OUTPUT


def test_use_case_configuration_content_is_visible_to_llm(make_pipeline):
    repo = FakeCodeRepository()
    llm = scripted_llm()
    make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, True])).run(new_run())
    prompt = llm.calls_named("sdd.write_impl")[0]["user"]
    # FakeScaffolder 沒有 UseCaseConfiguration；確認骨架檔（ArchitectureTest）只列路徑、內容不進既有檔案
    assert "ArchitectureTest.java" in prompt and "class A {}" not in prompt


def test_spring_annotations_in_application_are_stripped_automatically(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    annotated = (
        f"=== FILE: application/src/main/java/{base_path}/application/usecase/CreateProfileService.java ===\n"
        f"package {DEFAULT_BASE_PACKAGE}.application.usecase;\nimport org.springframework.stereotype.Service;\n"
        "@Service\npublic class CreateProfileService {}\n"
    )
    llm = scripted_llm(**{"sdd.write_impl": [annotated]})
    repo = FakeCodeRepository()
    run = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, True])).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    written = repo.files[f"application/src/main/java/{base_path}/application/usecase/CreateProfileService.java"]
    assert "@Service" not in written and "org.springframework" not in written
    assert len(llm.calls_named("sdd.write_impl")) == 2  # 每個 task 一次，不需重試


def test_repeated_identical_failure_escalates_to_high_effort(make_pipeline):
    llm = scripted_llm()
    # 骨架 OK；T1 RED 失敗；GREEN 失敗 ×3（相同輸出）→ 第 3 次修正時應升級；之後成功
    run = make_pipeline(llm=llm, builder=FakeBuildRunner([True, False, False, False, False, True])).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    fixes = llm.calls_named("sdd.fix")
    assert fixes[0]["effort"] == "normal" and fixes[1]["effort"] == "high"
    assert "完全相同" in fixes[1]["user"]
    assert any("高推理模式" in e.message for e in run.events)


def test_third_parse_attempt_uses_high_effort(make_pipeline):
    good = scripted_llm()._scripts["sdd.specify"][0]
    llm = scripted_llm(**{"sdd.specify": ["garbage", "garbage", good]})
    run = make_pipeline(llm=llm, builder=FakeBuildRunner([True, False, True])).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    efforts = [c["effort"] for c in llm.calls_named("sdd.specify")]
    assert efforts == ["normal", "normal", "high"]


def test_deleting_tests_to_pass_is_rejected(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, FIX_OUTPUT, _fill

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    cheat = f"=== DELETE FILE: domain/src/test/java/{base_path}/domain/ProfileTest.java ===\n" + _fill(
        FIX_OUTPUT, DEFAULT_BASE_PACKAGE
    )
    llm = scripted_llm(**{"sdd.fix": [cheat, _fill(FIX_OUTPUT, DEFAULT_BASE_PACKAGE)]})
    repo = FakeCodeRepository()
    run = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, False, True])).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert f"domain/src/test/java/{base_path}/domain/ProfileTest.java" in repo.files
    assert "不可刪除測試檔案" in llm.calls_named("sdd.fix")[1]["user"]


def test_concrete_classes_without_tests_trigger_a_second_red_request(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, TEST_OUTPUT, _fill

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    controller_only = (
        f"=== FILE: adapter-web/src/main/java/{base_path}/adapter/web/C.java ===\n"
        f"package {DEFAULT_BASE_PACKAGE}.adapter.web;\n@RestController public class C {{}}\n"
    )
    llm = scripted_llm(**{"sdd.write_test": [controller_only, _fill(TEST_OUTPUT, DEFAULT_BASE_PACKAGE)]})
    run = make_pipeline(llm=llm, builder=FakeBuildRunner([True, False, True])).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    calls = llm.calls_named("sdd.write_test")
    assert "必須先輸出" in calls[1]["user"]
    assert any("重新要求先寫測試" in e.message for e in run.events)


def test_use_case_configuration_cannot_be_deleted(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, IMPL_OUTPUT, FakeScaffolder, _fill

    class ScaffoldWithConfig(FakeScaffolder):
        def scaffold(self, project_name, base_package, feature_slug):
            files = super().scaffold(project_name, base_package, feature_slug)
            path = f"bootstrap/src/main/java/{base_package.replace('.', '/')}/bootstrap/UseCaseConfiguration.java"
            from java_coding_agent.domain.models import GeneratedFile

            return [
                *files,
                GeneratedFile(path, f"package {base_package}.bootstrap;\nclass UseCaseConfiguration {{}}\n"),
            ]

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    delete_cfg = (
        f"=== DELETE FILE: bootstrap/src/main/java/{base_path}/bootstrap/UseCaseConfiguration.java ===\n"
        + _fill(IMPL_OUTPUT, DEFAULT_BASE_PACKAGE)
    )
    llm = scripted_llm(**{"sdd.write_impl": [delete_cfg, _fill(IMPL_OUTPUT, DEFAULT_BASE_PACKAGE)]})
    repo = FakeCodeRepository()
    pipeline = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, True]))
    pipeline._scaffolder = ScaffoldWithConfig()
    run = pipeline.run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert f"bootstrap/src/main/java/{base_path}/bootstrap/UseCaseConfiguration.java" in repo.files
    assert "不可修改或刪除" in llm.calls_named("sdd.write_impl")[1]["user"]


def test_delete_and_rewrite_of_use_case_configuration_is_treated_as_overwrite(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, IMPL_OUTPUT, FakeScaffolder, _fill

    class ScaffoldWithConfig(FakeScaffolder):
        def scaffold(self, project_name, base_package, feature_slug):
            files = super().scaffold(project_name, base_package, feature_slug)
            path = f"bootstrap/src/main/java/{base_package.replace('.', '/')}/bootstrap/UseCaseConfiguration.java"
            from java_coding_agent.domain.models import GeneratedFile

            return [
                *files,
                GeneratedFile(path, f"package {base_package}.bootstrap;\nclass UseCaseConfiguration {{}}\n"),
            ]

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    cfg_path = f"bootstrap/src/main/java/{base_path}/bootstrap/UseCaseConfiguration.java"
    rewrite = (
        f"=== DELETE FILE: {cfg_path} ===\n"
        f"=== FILE: {cfg_path} ===\npackage {DEFAULT_BASE_PACKAGE}.bootstrap;\n"
        "class UseCaseConfiguration { int v = 2; }\n"
        "=== END FILE ===\n"
    ) + _fill(IMPL_OUTPUT, DEFAULT_BASE_PACKAGE)
    llm = scripted_llm(**{"sdd.write_impl": [rewrite]})
    repo = FakeCodeRepository()
    pipeline = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, True]))
    pipeline._scaffolder = ScaffoldWithConfig()
    run = pipeline.run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert "int v = 2" in repo.files[cfg_path]
    assert not any("不可修改或刪除" in c["user"] for c in llm.calls_named("sdd.write_impl"))


def test_impl_output_must_implement_every_inbound_port(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, IMPL_OUTPUT, _fill

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    port_in = f"application/src/main/java/{base_path}/application/port/in"
    usecase = f"application/src/main/java/{base_path}/application/usecase"
    partial = (
        f"=== FILE: {port_in}/CreateProfileUseCase.java ===\npackage {DEFAULT_BASE_PACKAGE}.application.port.in;\n"
        "public interface CreateProfileUseCase {}\n=== END FILE ===\n"
        f"=== FILE: {port_in}/DeleteProfileUseCase.java ===\npackage {DEFAULT_BASE_PACKAGE}.application.port.in;\n"
        "public interface DeleteProfileUseCase {}\n=== END FILE ===\n"
        f"=== FILE: {usecase}/CreateProfileService.java ===\npackage {DEFAULT_BASE_PACKAGE}.application.usecase;\n"
        "public class CreateProfileService implements CreateProfileUseCase {}\n=== END FILE ===\n"
    )
    complete = partial + (
        f"=== FILE: {usecase}/DeleteProfileService.java ===\npackage {DEFAULT_BASE_PACKAGE}.application.usecase;\n"
        "public class DeleteProfileService implements DeleteProfileUseCase {}\n=== END FILE ===\n"
    )
    llm = scripted_llm(**{"sdd.write_impl": [partial, complete, _fill(IMPL_OUTPUT, DEFAULT_BASE_PACKAGE)]})
    repo = FakeCodeRepository()
    pipeline = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, True]))
    run = pipeline.run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert "DeleteProfileUseCase" in llm.calls_named("sdd.write_impl")[1]["user"]
    assert f"{usecase}/DeleteProfileService.java" in repo.files


def test_red_phase_may_not_delete_main_sources(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, TEST_OUTPUT, _fill

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    destructive = (
        f"=== DELETE FILE: application/src/main/java/{base_path}/application/port/in/CreateProfileRequest.java ===\n"
    ) + _fill(TEST_OUTPUT, DEFAULT_BASE_PACKAGE)
    llm = scripted_llm(**{"sdd.write_test": [destructive, _fill(TEST_OUTPUT, DEFAULT_BASE_PACKAGE)]})
    repo = FakeCodeRepository(
        existing={
            f"application/src/main/java/{base_path}/application/port/in/CreateProfileRequest.java": "record R() {}"
        }
    )
    run = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, True])).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert f"application/src/main/java/{base_path}/application/port/in/CreateProfileRequest.java" in repo.files
    assert "不可刪除主程式碼" in llm.calls_named("sdd.write_test")[1]["user"]


def test_impl_output_with_not_implemented_stub_is_rejected(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, IMPL_OUTPUT, _fill

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    stub = (
        f"=== FILE: domain/src/main/java/{base_path}/domain/Profile.java ===\npackage {DEFAULT_BASE_PACKAGE}.domain;\n"
        "public record Profile(String name) {\n    public Profile rename(String n) {\n"
        '        throw new UnsupportedOperationException("Not implemented yet");\n    }\n}\n=== END FILE ===\n'
    )
    llm = scripted_llm(**{"sdd.write_impl": [stub, _fill(IMPL_OUTPUT, DEFAULT_BASE_PACKAGE)]})
    repo = FakeCodeRepository()
    run = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, True])).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert "尚未實作" in llm.calls_named("sdd.write_impl")[1]["user"]
    assert "UnsupportedOperationException" not in repo.files[f"domain/src/main/java/{base_path}/domain/Profile.java"]


def test_red_phase_ignores_deletion_of_nonexistent_main_paths(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, TEST_OUTPUT, _fill

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    phantom = (
        f"=== DELETE FILE: adapter-persistence/src/main/java/{base_path}/adapter/persistence/converter/X.java ===\n"
    ) + _fill(TEST_OUTPUT, DEFAULT_BASE_PACKAGE)
    llm = scripted_llm(**{"sdd.write_test": [phantom]})
    run = make_pipeline(llm=llm, code_repo=FakeCodeRepository(), builder=FakeBuildRunner([True, False, True])).run(
        new_run()
    )
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert not any("不可刪除主程式碼" in c["user"] for c in llm.calls_named("sdd.write_test"))


def test_deleting_a_nonexistent_test_path_is_ignored(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, TEST_OUTPUT, _fill

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    phantom = (
        f"=== DELETE FILE: adapter-persistence/src/test/java/{base_path}/adapter/persistence/entity/"
        "ProfileEntityTest.java ===\n"
    ) + _fill(TEST_OUTPUT, DEFAULT_BASE_PACKAGE)
    llm = scripted_llm(**{"sdd.write_test": [phantom]})
    run = make_pipeline(llm=llm, code_repo=FakeCodeRepository(), builder=FakeBuildRunner([True, False, True])).run(
        new_run()
    )
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert not any("不可刪除測試檔案" in c["user"] for c in llm.calls_named("sdd.write_test"))


def test_bootstrap_wiring_task_skips_red_without_asking_the_llm(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, IMPL_OUTPUT, TASKS_OUTPUT, TEST_OUTPUT, _fill

    tasks = TASKS_OUTPUT.replace(
        "=== END FILE ===",
        "\n## T3 Bootstrap Configuration & Context Assembly\n- 說明：在 UseCaseConfiguration 註冊 @Bean\n"
        "- 測試：骨架 ApplicationContextTest\n\n## T4 Web Adapter\n- 說明：Controller\n- 測試：adapter-web/src/test\n"
        "=== END FILE ===",
    )
    llm = scripted_llm(
        **{
            "sdd.tasks": [tasks],
            "sdd.write_test": [_fill(TEST_OUTPUT, DEFAULT_BASE_PACKAGE)] * 3,
            "sdd.write_impl": [_fill(IMPL_OUTPUT, DEFAULT_BASE_PACKAGE)] * 4,
        }
    )
    # 骨架 OK；T1/T2/T4 各 RED 失敗、GREEN 成功；T3 無 RED、GREEN 成功；verify 成功
    builds = [True, False, True, False, True, True, False, True, True]
    run = make_pipeline(llm=llm, code_repo=FakeCodeRepository(), builder=FakeBuildRunner(builds)).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert len(llm.calls_named("sdd.write_test")) == 3  # T3 沒有向 LLM 要測試
    assert any("略過 RED 直接進入 GREEN" in e.message and "[T3]" in e.message for e in run.events)


def test_combined_persistence_and_assembly_task_still_gets_red(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, TASKS_OUTPUT, TEST_OUTPUT, _fill

    tasks = TASKS_OUTPUT.replace(
        "## T2 建立 CreateProfile use case",
        "## T2 Persistence Adapter & Configuration Assembly",
    )
    llm = scripted_llm(**{"sdd.tasks": [tasks], "sdd.write_test": [_fill(TEST_OUTPUT, DEFAULT_BASE_PACKAGE)] * 2})
    run = make_pipeline(
        llm=llm, code_repo=FakeCodeRepository(), builder=FakeBuildRunner([True, False, True, False, True, True])
    ).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert len(llm.calls_named("sdd.write_test")) == 2


def test_deleting_a_misplaced_main_class_under_src_test_is_allowed(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, IMPL_OUTPUT, TEST_OUTPUT, _fill

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    misplaced = f"bootstrap/src/test/java/{base_path}/bdd/GlobalExceptionHandler.java"
    red = _fill(TEST_OUTPUT, DEFAULT_BASE_PACKAGE) + (
        f"=== FILE: {misplaced} ===\npackage {DEFAULT_BASE_PACKAGE}.bdd;\n"
        "public class GlobalExceptionHandler {}\n=== END FILE ===\n"
    )
    green = f"=== DELETE FILE: {misplaced} ===\n" + _fill(IMPL_OUTPUT, DEFAULT_BASE_PACKAGE)
    llm = scripted_llm(**{"sdd.write_test": [red], "sdd.write_impl": [green]})
    repo = FakeCodeRepository()
    run = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, True])).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert misplaced not in repo.files
    assert not any("不可刪除測試檔案" in c["user"] for c in llm.calls_named("sdd.write_impl"))


def test_bdd_task_green_must_produce_a_feature_file(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, IMPL_OUTPUT, TEST_OUTPUT, _fill

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    steps_only = (
        f"=== FILE: bootstrap/src/test/java/{base_path}/bdd/ProfileSteps.java ===\n"
        f"package {DEFAULT_BASE_PACKAGE}.bdd;\nimport io.cucumber.java.zh_tw.當;\npublic class ProfileSteps {{}}\n"
        "=== END FILE ===\n"
    )
    with_feature = steps_only + (
        "=== FILE: bootstrap/src/test/resources/features/profile.feature ===\n"
        '# language: zh-TW\n功能: x\n  場景: y\n    當我發送 GET 請求至 "/x"\n=== END FILE ===\n'
    )
    # T1 一般；T2 改成 BDD 驗收任務：RED 只給步驟類別、GREEN 第一次也沒有 .feature → 被要求補上
    from tests.unit.fakes import TASKS_OUTPUT

    tasks = TASKS_OUTPUT.replace("## T2 建立 CreateProfile use case", "## T2 BDD 驗收測試（Cucumber）")
    llm = scripted_llm(
        **{
            "sdd.tasks": [tasks],
            "sdd.write_test": [_fill(TEST_OUTPUT, DEFAULT_BASE_PACKAGE), steps_only],
            "sdd.write_impl": [_fill(IMPL_OUTPUT, DEFAULT_BASE_PACKAGE), steps_only, with_feature],
        }
    )
    repo = FakeCodeRepository()
    run = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, True, True, True, True])).run(
        new_run()
    )
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert "bootstrap/src/test/resources/features/profile.feature" in repo.files
    assert any("輸出缺少路徑含 `.feature`" in c["user"] for c in llm.calls_named("sdd.write_impl"))


def test_red_phase_that_never_yields_tests_is_skipped_instead_of_failing(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, TEST_OUTPUT, _fill

    # T1 的 RED 永遠回傳沒有檔案區塊的文字；T2 正常
    garbage = "我想先分析一下這個任務……（沒有任何 FILE 區塊）"
    llm = scripted_llm(**{"sdd.write_test": [garbage, garbage, garbage, _fill(TEST_OUTPUT, DEFAULT_BASE_PACKAGE)]})
    repo = FakeCodeRepository()
    # 骨架 OK；T1 無 RED 執行、GREEN 成功；T2 RED 失敗、GREEN 成功；verify 成功
    run = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, True, False, True, True])).run(
        new_run()
    )
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert any("略過 RED 直接進入 GREEN" in e.message for e in run.events)


def test_existing_tests_get_imports_when_green_creates_the_types(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, IMPL_OUTPUT, _fill

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    app = f"application/src/main/java/{base_path}/application"
    test_path = f"application/src/test/java/{base_path}/application/usecase/CreateProfileServiceTest.java"
    red = (
        f"=== FILE: {test_path} ===\npackage {DEFAULT_BASE_PACKAGE}.application.usecase;\n"
        "class CreateProfileServiceTest {\n    private SaveProfilePort savePort;\n}\n=== END FILE ===\n"
    )
    green = (
        f"=== FILE: {app}/port/out/SaveProfilePort.java ===\npackage {DEFAULT_BASE_PACKAGE}.application.port.out;\n"
        "public interface SaveProfilePort {}\n=== END FILE ===\n"
        f"=== FILE: {app}/usecase/CreateProfileService.java ===\npackage {DEFAULT_BASE_PACKAGE}.application.usecase;\n"
        "public class CreateProfileService { SaveProfilePort p; }\n=== END FILE ===\n"
    ) + _fill(IMPL_OUTPUT, DEFAULT_BASE_PACKAGE)
    llm = scripted_llm(**{"sdd.write_test": [red], "sdd.write_impl": [green]})
    repo = FakeCodeRepository()
    run = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, True])).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert f"import {DEFAULT_BASE_PACKAGE}.application.port.out.SaveProfilePort;" in repo.files[test_path]
    assert (
        f"import {DEFAULT_BASE_PACKAGE}.application.port.out.SaveProfilePort;"
        in repo.files[f"{app}/usecase/CreateProfileService.java"]
    )


def test_feature_files_are_only_allowed_in_the_bdd_task(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, TEST_OUTPUT, _fill

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    premature = (
        "=== FILE: bootstrap/src/test/resources/features/profile.feature ===\n# language: zh-TW\n功能: x\n"
        '  場景: a\n    當 我呼叫 GET "/x"\n=== END FILE ===\n'
        f"=== FILE: bootstrap/src/test/java/{base_path}/bdd/ProfileSteps.java ===\n"
        f"package {DEFAULT_BASE_PACKAGE}.bdd;\n"
        "import io.cucumber.java.zh_tw.當;\npublic class ProfileSteps {}\n=== END FILE ===\n"
    )
    # T1（domain）RED 想寫 Cucumber → 被拒絕後改回正常測試；T2 是最後一個任務，允許
    llm = scripted_llm(**{"sdd.write_test": [premature, _fill(TEST_OUTPUT, DEFAULT_BASE_PACKAGE)]})
    repo = FakeCodeRepository()
    run = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, True])).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert "屬於最後的 BDD 任務" in llm.calls_named("sdd.write_test")[1]["user"]
    assert not any(p.endswith(".feature") for p in repo.files)


def test_spec_is_written_next_to_plan_and_tasks(make_pipeline):
    # 模型把 spec 放到 specs/001-{slug}/ 以外的目錄時，仍要落在 specs/<id>-<feature_slug>/spec.md
    stray = (
        "=== FILE: specs/001-personal-profile/spec.md ===\n# Spec\n=== END FILE ===\n"
        "FEATURE_NAME: personal-profile-crud\n"
    )
    llm = scripted_llm(**{"sdd.specify": [stray]})
    repo = FakeCodeRepository()
    run = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, True])).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert "specs/001-personal-profile-crud/spec.md" in repo.files
    assert "specs/001-personal-profile/spec.md" not in repo.files


def test_green_may_add_tests_when_red_was_skipped(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, IMPL_OUTPUT, TEST_OUTPUT, _fill

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    garbage = "沒有任何 FILE 區塊"
    # T2（最後一個任務）RED 失敗被略過；GREEN 同時補上 bootstrap 的 bdd 步驟類別 → 不可因「模組為空」被拒
    green_with_steps = (
        f"=== FILE: bootstrap/src/test/java/{base_path}/bdd/ProfileSteps.java ===\n"
        f"package {DEFAULT_BASE_PACKAGE}.bdd;\n"
        "import io.cucumber.java.zh_tw.當;\npublic class ProfileSteps {}\n=== END FILE ===\n"
    ) + _fill(IMPL_OUTPUT, DEFAULT_BASE_PACKAGE)
    llm = scripted_llm(
        **{
            "sdd.write_test": [_fill(TEST_OUTPUT, DEFAULT_BASE_PACKAGE), garbage, garbage, garbage],
            "sdd.write_impl": [_fill(IMPL_OUTPUT, DEFAULT_BASE_PACKAGE), green_with_steps],
        }
    )
    repo = FakeCodeRepository()
    run = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, True, True, True])).run(
        new_run()
    )
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert f"bootstrap/src/test/java/{base_path}/bdd/ProfileSteps.java" in repo.files
    assert not any("超出本任務範圍" in c["user"] for c in llm.calls_named("sdd.write_impl"))


def test_new_tests_outside_task_modules_are_rejected(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, IMPL_OUTPUT, _fill

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    wander = (
        f"=== FILE: bootstrap/src/test/java/{base_path}/bootstrap/ApplicationContextTest.java ===\n"
        f"package {DEFAULT_BASE_PACKAGE}.bootstrap;\nclass ApplicationContextTest {{}}\n"
    ) + _fill(IMPL_OUTPUT, DEFAULT_BASE_PACKAGE)
    llm = scripted_llm(**{"sdd.write_impl": [wander, _fill(IMPL_OUTPUT, DEFAULT_BASE_PACKAGE)]})
    repo = FakeCodeRepository()
    run = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, True])).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert not any(p.endswith("ApplicationContextTest.java") for p in repo.files)
    assert "超出本任務範圍" in llm.calls_named("sdd.write_impl")[1]["user"]


def test_red_tests_are_limited_to_task_modules_from_tasks_md(make_pipeline):
    from tests.unit.fakes import DEFAULT_BASE_PACKAGE, TEST_OUTPUT, _fill

    base_path = DEFAULT_BASE_PACKAGE.replace(".", "/")
    # tasks.md 的 T1 測試提示指向 domain/src/test；RED 卻在 bootstrap 新增步驟類別 → 被拒後改回 domain 測試
    wander = (
        f"=== FILE: bootstrap/src/test/java/{base_path}/bdd/ProfileSteps.java ===\n"
        f"package {DEFAULT_BASE_PACKAGE}.bdd;\nclass ProfileSteps {{}}\n"
    ) + _fill(TEST_OUTPUT, DEFAULT_BASE_PACKAGE)
    llm = scripted_llm(**{"sdd.write_test": [wander, _fill(TEST_OUTPUT, DEFAULT_BASE_PACKAGE)]})
    repo = FakeCodeRepository()
    run = make_pipeline(llm=llm, code_repo=repo, builder=FakeBuildRunner([True, False, True])).run(new_run())
    assert run.stage is RunStage.SUCCEEDED, run.error
    assert not any(p.endswith("ProfileSteps.java") for p in repo.files)
    feedback = llm.calls_named("sdd.write_test")[1]["user"]
    assert "超出本任務範圍" in feedback or "屬於最後的 BDD 任務" in feedback
