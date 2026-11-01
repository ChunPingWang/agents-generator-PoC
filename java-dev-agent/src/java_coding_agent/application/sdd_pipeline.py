"""SDD 流程（LangGraph 狀態圖）：

prepare(workspace+骨架) → specify → plan → tasks → implement(每個 task 一輪 RED/GREEN) → verify → publish

GitHub 可見性：每個主要階段 commit 後立即推送 feature 分支；tasks.md 完成即開 Draft PR（含 checklist），
每完成一個 task 更新勾選；全部驗證通過後把 Draft 轉為 Ready for review。失敗時維持 Draft 並寫入失敗摘要。

所有外部效果（LLM、Git/GitHub、Gradle、骨架）皆透過 ports 注入，流程本身可用 fakes 完整測試。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict

from langchain_core.callbacks import BaseCallbackHandler
from langgraph.graph import END, START, StateGraph

from java_coding_agent.application.templates import PromptTemplates
from java_coding_agent.domain.errors import DomainError, LlmOutputError
from java_coding_agent.domain.file_blocks import parse_deletions, parse_file_blocks, render_file_blocks
from java_coding_agent.domain.java_naming import base_package_for, package_path, project_name_for
from java_coding_agent.domain.java_paths import (
    add_missing_project_imports,
    complete_webmvc_mocks,
    cucumber_precondition_violations,
    cucumber_step_violations,
    duplicate_simple_names,
    framework_violations,
    gherkin_violations,
    package_violations,
    path_variable_violations,
    persistence_violations,
    project_type_index,
    shadowing_violations,
    stub_violations,
    truncated_java_files,
    unimplemented_use_cases,
    uniqueness_test_violations,
    webmvc_controller_name,
)
from java_coding_agent.domain.models import (
    FeatureRun,
    GeneratedFile,
    RepoSettings,
    RunStage,
    TaskItem,
    slugify,
)
from java_coding_agent.domain.ports import (
    BuildRunnerPort,
    CodeRepositoryPort,
    EventSink,
    LlmPort,
    PromptStorePort,
    ScaffolderPort,
    SettingsRepository,
)
from java_coding_agent.domain.tasks_md import parse_tasks_md, render_tasks_md

_TEST_MARKER = re.compile(
    r"@Test\b|@ParameterizedTest\b|io\.cucumber|@SpringBootTest|@WebMvcTest|@DataJpaTest|@Nested\b"
)


def _looks_like_test(path: str, content: str) -> bool:
    """src/test 下真正的測試（或 Cucumber glue / .feature）才受「不可刪除」保護；模型放錯位置的主程式類別可以刪。"""
    name = path.rsplit("/", 1)[-1]
    if path.endswith(".feature") or name.endswith(("Test.java", "Tests.java", "IT.java", "Steps.java")):
        return True
    return bool(_TEST_MARKER.search(content))


_FEATURE_NAME = re.compile(r"^FEATURE_NAME:\s*([a-z0-9-]+)", re.M)
_SOURCE_PATTERNS = ("**/*.java", "**/*.feature", "specs/**/*.md")


@dataclass(frozen=True)
class PipelineOptions:
    max_fix_attempts: int = 10
    max_parse_retries: int = 2
    strict_red: bool = False
    """True 時，若測試在無實作下就通過，視為流程失敗；False 僅記錄警告。"""


class _State(TypedDict, total=False):
    run: FeatureRun
    repo: RepoSettings
    workspace: Path
    branch: str
    base_package: str
    project_name: str
    feature_id: str
    scaffold_paths: list[str]
    task_index: int
    done: bool


class SddPipeline:
    def __init__(
        self,
        *,
        llm: LlmPort,
        code_repo: CodeRepositoryPort,
        builder: BuildRunnerPort,
        scaffolder: ScaffolderPort,
        prompt_store: PromptStorePort,
        settings: SettingsRepository,
        templates: PromptTemplates,
        workspace_root: Path,
        options: PipelineOptions | None = None,
        on_event: EventSink | None = None,
    ) -> None:
        self._llm = llm
        self._repo = code_repo
        self._builder = builder
        self._scaffolder = scaffolder
        self._prompt_store = prompt_store
        self._settings = settings
        self._templates = templates
        self._workspace_root = workspace_root
        self._opt = options or PipelineOptions()
        self._on_event = on_event or (lambda run: None)
        self._states: dict[str, _State] = {}
        """每個 run 最近一次的 graph state；供 run() 的例外處理推送狀態並更新 Draft PR。"""
        self._graph = self._build_graph()

    # ------------------------------------------------------------------ public
    def run(self, run: FeatureRun, callbacks: Sequence[BaseCallbackHandler] | None = None) -> FeatureRun:
        config: dict[str, Any] = {
            "recursion_limit": 200,
            "callbacks": list(callbacks or []),
            "run_name": f"sdd-run-{run.id}",
            "metadata": {"run_id": run.id},
        }
        try:
            final = self._graph.invoke({"run": run}, config=config)
            return final["run"]
        except DomainError as exc:
            if not run.stage.is_terminal:
                self._fail(self._states.get(run.id, {"run": run}), run, run.stage, str(exc))
            return run
        except Exception as exc:  # 非預期錯誤也必須讓 Run 進入終態
            if not run.stage.is_terminal:
                self._fail(self._states.get(run.id, {"run": run}), run, run.stage, f"{type(exc).__name__}: {exc}")
            return run
        finally:
            self._states.pop(run.id, None)

    # ------------------------------------------------------------------ graph
    def _build_graph(self):
        g: StateGraph = StateGraph(_State)
        g.add_node("prepare", self._prepare)
        g.add_node("specify", self._specify)
        g.add_node("plan", self._plan)
        g.add_node("tasks", self._tasks)
        g.add_node("implement_task", self._implement_task)
        g.add_node("verify", self._verify)
        g.add_node("publish", self._publish)

        g.add_edge(START, "prepare")
        for src, dst in (("prepare", "specify"), ("specify", "plan"), ("plan", "tasks"), ("tasks", "implement_task")):
            g.add_conditional_edges(src, self._continue_or_end(dst), {dst: dst, END: END})
        g.add_conditional_edges(
            "implement_task",
            self._next_task_or_verify,
            {"implement_task": "implement_task", "verify": "verify", END: END},
        )
        g.add_conditional_edges("verify", self._continue_or_end("publish"), {"publish": "publish", END: END})
        g.add_edge("publish", END)
        return g.compile()

    @staticmethod
    def _continue_or_end(next_node: str):
        def router(state: _State) -> str:
            return END if state["run"].stage.is_terminal else next_node

        return router

    @staticmethod
    def _next_task_or_verify(state: _State) -> str:
        run = state["run"]
        if run.stage.is_terminal:
            return END
        return "implement_task" if state["task_index"] < len(run.tasks) else "verify"

    # ------------------------------------------------------------------ nodes
    def _prepare(self, state: _State) -> _State:
        run = state["run"]
        repo = self._settings.load_repo_settings()
        self._record(run, RunStage.SCAFFOLD, f"準備 workspace，目標 repo {repo.full_name}（base: {repo.base_branch}）")
        branch = f"feature/{run.id[:8]}-{slugify(run.requirement) if slugify(run.requirement) != 'feature' else 'sdd'}"
        run.branch = branch
        workspace = self._repo.prepare_workspace(repo, branch, self._workspace_root / run.id)
        base_package = base_package_for(repo)
        project_name = project_name_for(repo)
        existing_specs = self._repo.read_files(workspace, ("specs/*/spec.md",))
        feature_id = f"{len(existing_specs) + 1:03d}"

        scaffold = self._scaffolder.scaffold(project_name, base_package, feature_id)
        scaffold_paths = [f.path for f in scaffold]
        full_state: _State = {
            "run": run,
            "repo": repo,
            "workspace": workspace,
            "branch": branch,
            "base_package": base_package,
            "project_name": project_name,
            "feature_id": feature_id,
            "scaffold_paths": scaffold_paths,
            "task_index": 0,
        }
        self._states[run.id] = full_state

        if not self._repo.read_files(workspace, ("settings.gradle.kts",)):
            self._repo.write_files(workspace, scaffold)
            self._commit_and_push(full_state, "chore: 建立 Gradle 多模組六角形架構骨架")
            self._record(
                run, RunStage.SCAFFOLD, f"已建立專案骨架（{len(scaffold)} 個檔案）並推送分支，先驗證骨架可建置"
            )
            result = self._builder.run_tests(workspace)
            if not result.success:
                self._fail(full_state, run, RunStage.SCAFFOLD, f"骨架建置失敗：\n{result.summary()}")
                return {"run": run}
        else:
            self._record(run, RunStage.SCAFFOLD, "repo 已有骨架，沿用既有專案")
        return full_state

    def _specify(self, state: _State) -> _State:
        run = state["run"]
        self._record(run, RunStage.SPECIFY, "撰寫 spec.md")
        run.feature_slug = run.feature_slug or "feature"
        prompt = self._templates.render(
            "sdd_specify.md", requirement=run.requirement, feature_id=state["feature_id"], feature_slug="{slug}"
        )
        text, files = self._ask_for_files(run, prompt, name="sdd.specify", expect_paths_containing="spec.md")
        match = _FEATURE_NAME.search(text)
        run.feature_slug = slugify(match.group(1)) if match else "feature"
        # spec 一律放在 specs/<feature_id>-<slug>/spec.md，與 plan.md / tasks.md 同一目錄（模型常自行取別的目錄名）
        spec_file = GeneratedFile(
            path=f"specs/{state['feature_id']}-{run.feature_slug}/spec.md", content=files[0].content
        )
        run.spec_md = spec_file.content
        self._repo.write_files(state["workspace"], [spec_file])
        self._record(run, RunStage.SPECIFY, f"spec.md 完成（功能：{run.feature_slug}）", path=spec_file.path)
        return {"run": run}

    def _plan(self, state: _State) -> _State:
        run = state["run"]
        self._record(run, RunStage.PLAN, "撰寫 plan.md")
        prompt = self._templates.render(
            "sdd_plan.md",
            spec_md=run.spec_md,
            feature_id=state["feature_id"],
            feature_slug=run.feature_slug,
            base_package=state["base_package"],
            base_package_path=package_path(state["base_package"]),
        )
        _, files = self._ask_for_files(run, prompt, name="sdd.plan", expect_paths_containing="plan.md")
        run.plan_md = files[0].content
        self._repo.write_files(state["workspace"], files[:1])
        self._record(run, RunStage.PLAN, "plan.md 完成", path=files[0].path)
        return {"run": run}

    def _tasks(self, state: _State) -> _State:
        run = state["run"]
        self._record(run, RunStage.TASKS, "拆解 tasks.md")
        prompt = self._templates.render(
            "sdd_tasks.md", plan_md=run.plan_md, feature_id=state["feature_id"], feature_slug=run.feature_slug
        )
        last_error = ""
        for attempt in range(self._opt.max_parse_retries + 1):
            text = self._llm.complete(
                self._system_prompt(state), prompt + last_error, name="sdd.tasks", metadata=self._meta(run)
            )
            try:
                files = parse_file_blocks(text)
                run.tasks = parse_tasks_md(files[0].content)
                break
            except LlmOutputError as exc:
                last_error = f"\n\n上一次輸出無法解析：{exc}。請嚴格依格式重新輸出。"
                self._record(run, RunStage.TASKS, f"tasks.md 解析失敗（第 {attempt + 1} 次）：{exc}", level="warning")
        else:
            self._fail(state, run, RunStage.TASKS, f"tasks.md 多次無法解析：{last_error}")
            return {"run": run}

        self._repo.write_files(state["workspace"], files[:1])
        self._commit_and_push(state, f"docs: spec/plan/tasks for {run.feature_slug}")
        self._record(
            run, RunStage.TASKS, f"tasks.md 完成，共 {len(run.tasks)} 個任務", tasks=[t.title for t in run.tasks]
        )
        pr = self._repo.open_pull_request(
            state["repo"],
            state["branch"],
            self._pr_title(state, run, wip=True),
            self._progress_body(run, "實作中（TDD）"),
            draft=True,
        )
        run.pull_request = pr
        self._record(run, RunStage.TASKS, f"已建立 Draft PR：{pr.url}", pr_url=pr.url)
        return {"run": run, "task_index": 0}

    def _implement_task(self, state: _State) -> _State:
        run = state["run"]
        index = state["task_index"]
        task = run.tasks[index]
        workspace = state["workspace"]
        ctx = self._task_context(state, task)
        # 只有 BDD / 驗收任務（或最後一個任務）可以寫 .feature 與 bdd 步驟類別
        allow_bdd = index == len(run.tasks) - 1 or bool(
            re.search(r"cucumber|bdd|驗收|acceptance|gherkin", task.title + " " + task.description, re.I)
        )
        # 組裝/組態任務：模型幾乎寫不出可用的測試（總是想改骨架的 ApplicationContextTest 或提前寫 Cucumber），
        # 每次都要浪費 3 次 LLM 呼叫才落到 RED fallback；骨架的 ApplicationContextTest 已驗證 context 能啟動，
        # 直接略過 RED
        is_wiring = (
            not allow_bdd
            and bool(re.search(r"bootstrap|wiring|組態|組裝|接線|assembly", task.title, re.I))
            # 「Persistence Adapter & Configuration Assembly」這種合併任務仍含具體類別，必須寫測試
            and not re.search(
                r"adapter|persistence|repository|entity|controller|web|持久|控制器|轉接", task.title, re.I
            )
        )
        red_modules = _modules_in_text(task.description + " " + task.test_hint)
        red_files: list[GeneratedFile] = []
        test_files: list[GeneratedFile] = []
        if is_wiring:
            self._record(
                run,
                RunStage.IMPLEMENT,
                f"[{task.id}] {task.title}：組裝/組態任務由骨架的 ApplicationContextTest 驗證，略過 RED 直接進入 GREEN",
                level="warning",
                task=task.id,
            )
        else:
            self._record(run, RunStage.IMPLEMENT, f"[{task.id}] {task.title}：RED，撰寫測試", task=task.id)

            # RED ------------------------------------------------------------
            prompt = self._templates.render("sdd_write_test.md", **ctx)
            try:
                _, red_files = self._ask_for_files(
                    run,
                    prompt,
                    name="sdd.write_test",
                    workspace=workspace,
                    base_package=state["base_package"],
                    protected_paths=state["scaffold_paths"],
                    allow_bdd=allow_bdd,
                    allowed_test_modules=sorted(red_modules) if red_modules else None,
                )
            except LlmOutputError as exc:
                # RED 寫不出可用的測試（例如組裝/接線任務）不應讓整個 run 失敗：略過 RED，由骨架與後續任務的測試驗證
                self._record(
                    run,
                    RunStage.IMPLEMENT,
                    f"[{task.id}] RED 無法產生可用的測試（{str(exc)[:120]}），略過 RED 直接進入 GREEN",
                    level="warning",
                    task=task.id,
                )
                red_files = []
            test_files = [f for f in red_files if "/src/test/" in f.path]
            if not test_files and _needs_tests(red_files):
                # 產出的是具體類別（Controller / Service / Entity 等），不能以「純介面」為由略過測試：再要求一次
                self._record(
                    run,
                    RunStage.IMPLEMENT,
                    f"[{task.id}] RED 輸出只有實作類別而沒有測試，重新要求先寫測試",
                    level="warning",
                    task=task.id,
                )
                try:
                    _, red_files = self._ask_for_files(
                        run,
                        prompt
                        + "\n\n**上一次輸出沒有任何測試檔案。這個任務包含具體類別，必須先輸出 `src/test/java` 下的測試"
                        "（例如 `@WebMvcTest` 控制器測試、`@DataJpaTest`、或純 JUnit 測試），實作留到下一步。**",
                        name="sdd.write_test",
                        expect_paths_containing="/src/test/",
                        workspace=workspace,
                        base_package=state["base_package"],
                        protected_paths=state["scaffold_paths"],
                        allow_bdd=allow_bdd,
                    )
                except LlmOutputError as exc:
                    self._record(
                        run,
                        RunStage.IMPLEMENT,
                        f"[{task.id}] 再次要求仍無法取得測試（{str(exc)[:120]}），略過 RED 直接進入 GREEN",
                        level="warning",
                        task=task.id,
                    )
                    red_files = []
                test_files = [f for f in red_files if "/src/test/" in f.path]
        self._repo.write_files(workspace, red_files)
        if not test_files:
            # 純介面 / DTO 任務沒有可寫的測試：記錄警告並直接進入 GREEN（由後續任務的測試覆蓋）
            self._record(
                run,
                RunStage.IMPLEMENT,
                f"[{task.id}] 此任務未產生測試（可能為純介面或 DTO），略過 RED 階段",
                level="warning",
                task=task.id,
                files=[f.path for f in red_files],
            )
        else:
            red = self._builder.run_tests(workspace)
            if red.success:
                msg = f"[{task.id}] 測試在尚無實作時即通過，不符合 TDD 的 RED 階段"
                if self._opt.strict_red:
                    self._fail(state, run, RunStage.IMPLEMENT, msg)
                    return {"run": run}
                self._record(run, RunStage.IMPLEMENT, msg, level="warning", task=task.id)
            else:
                self._record(run, RunStage.IMPLEMENT, f"[{task.id}] RED 確認：測試如預期失敗", task=task.id)

        # 本任務觸及的模組（由 RED 輸出決定）：之後的實作/修正不得在其他模組新增測試
        # GREEN / 修正階段允許新增測試的模組：RED 寫過的模組；RED 被略過時退回 tasks.md 指到的模組；
        # BDD 任務永遠允許 bootstrap。都沒有則不限制（空集合會把所有測試都擋掉）。
        task_module_set = {f.path.split("/")[0] for f in red_files if "/" in f.path} or set(red_modules)
        if allow_bdd:
            task_module_set.add("bootstrap")
        task_modules = sorted(task_module_set) if task_module_set else None

        # GREEN ----------------------------------------------------------
        self._record(run, RunStage.IMPLEMENT, f"[{task.id}] GREEN，撰寫實作", task=task.id)
        ctx = self._task_context(state, task)
        prompt = self._templates.render(
            "sdd_write_impl.md",
            test_files=render_file_blocks(test_files)
            if test_files
            else "（本任務無專屬測試；請確保程式可編譯且既有測試仍通過）",
            **ctx,
        )
        # BDD 任務若到 GREEN 還沒有任何（非 smoke 的）.feature，GREEN 必須補上，否則驗收任務會「空過」
        is_bdd_task = bool(
            re.search(r"cucumber|bdd|驗收|acceptance|gherkin", task.title + " " + task.description, re.I)
        )
        has_feature = any(
            not f.path.endswith("smoke.feature")
            for f in self._repo.read_files(workspace, ("bootstrap/src/test/resources/features/*.feature",))
        )
        _, impl_files = self._ask_for_files(
            run,
            prompt
            + (
                "\n\n**本任務是 BDD 驗收任務，但目前還沒有任何 .feature 檔；請在這次輸出一併給出 "
                "`bootstrap/src/test/resources/features/<feature>.feature` 與對應的步驟類別。**"
                if is_bdd_task and not has_feature
                else ""
            ),
            name="sdd.write_impl",
            expect_paths_containing=".feature" if is_bdd_task and not has_feature else None,
            workspace=workspace,
            base_package=state["base_package"],
            protected_paths=state["scaffold_paths"],
            allow_bdd=allow_bdd,
            allowed_test_modules=task_modules,
        )
        self._repo.write_files(workspace, impl_files)
        result = self._builder.run_tests(workspace)

        attempt = 0
        previous_summary = ""
        repeats = 0
        while not result.success and attempt < self._opt.max_fix_attempts:
            attempt += 1
            summary = result.summary()
            repeats = repeats + 1 if summary == previous_summary else 0
            previous_summary = summary
            stuck = repeats >= 1  # 連續兩次完全相同的錯誤：上一次修正毫無效果
            self._record(
                run,
                RunStage.IMPLEMENT,
                f"[{task.id}] 測試失敗，修正第 {attempt} 次",
                level="warning",
                task=task.id,
                build_output=result.summary(2000),
            )
            ctx = self._task_context(state, task)
            prompt = self._templates.render(
                "sdd_fix.md",
                build_output=summary,
                attempt=attempt,
                max_attempts=self._opt.max_fix_attempts,
                stuck=stuck,
                **ctx,
            )
            if stuck:
                self._record(
                    run,
                    RunStage.IMPLEMENT,
                    f"[{task.id}] 連續 {repeats + 1} 次相同錯誤，改以高推理模式重寫相關檔案",
                    level="warning",
                    task=task.id,
                )
            _, fixes = self._ask_for_files(
                run,
                prompt,
                name="sdd.fix",
                workspace=workspace,
                base_package=state["base_package"],
                protected_paths=state["scaffold_paths"],
                allow_bdd=allow_bdd,
                effort="high" if stuck else "normal",
                allowed_test_modules=task_modules,
            )
            self._repo.write_files(workspace, fixes)
            result = self._builder.run_tests(workspace)

        if not result.success:
            self._fail(
                state, run, RunStage.IMPLEMENT, f"[{task.id}] 修正 {attempt} 次後測試仍失敗：\n{result.summary(1500)}"
            )
            return {"run": run}

        run.tasks[index] = task.completed()
        self._commit_and_push(state, f"feat({task.id}): {task.title}")
        self._update_pr(state, run, body=self._progress_body(run, "實作中（TDD）"))
        self._record(run, RunStage.IMPLEMENT, f"[{task.id}] GREEN 確認：測試通過，已 commit 並推送", task=task.id)
        return {"run": run, "task_index": index + 1}

    def _verify(self, state: _State) -> _State:
        run = state["run"]
        self._record(run, RunStage.VERIFY, "執行完整驗證（全部測試、ArchUnit、Cucumber）")
        result = self._builder.run_tests(state["workspace"])
        if not result.success:
            self._fail(state, run, RunStage.VERIFY, f"完整測試未通過：\n{result.summary(1500)}")
            return {"run": run}
        tasks_md = render_tasks_md(run.feature_slug, run.tasks)
        path = f"specs/{state['feature_id']}-{run.feature_slug}/tasks.md"
        self._repo.write_files(state["workspace"], [GeneratedFile(path, tasks_md)])
        self._commit_and_push(state, "docs: 標記 tasks 完成")
        self._record(run, RunStage.VERIFY, "完整驗證通過")
        return {"run": run}

    def _publish(self, state: _State) -> _State:
        run = state["run"]
        repo, branch, workspace = state["repo"], state["branch"], state["workspace"]
        self._record(run, RunStage.PUBLISH, f"推送分支 {branch} 並將 PR 標記為可審查")
        self._repo.push_branch(workspace, branch)
        title = self._pr_title(state, run, wip=False)
        try:
            body = self._llm.complete(
                self._system_prompt(state),
                self._templates.render("sdd_pr_body.md", spec_md=run.spec_md, tasks=run.tasks),
                name="sdd.pr_body",
                metadata=self._meta(run),
            )
        except Exception as exc:  # PR 描述失敗不應讓整個 Run 失敗
            self._record(run, RunStage.PUBLISH, f"LLM 產生 PR 描述失敗，改用預設描述：{exc}", level="warning")
            body = ""
        body = self._ensure_checklist(body, run.tasks, run.requirement)
        if run.pull_request is None:
            pr = self._repo.open_pull_request(repo, branch, title, body)
        else:
            pr = self._repo.update_pull_request(repo, run.pull_request, title=title, body=body, ready_for_review=True)
        run.succeed(pr)
        self._emit(run)
        return {"run": run}

    # ------------------------------------------------------------------ git / PR helpers
    def _commit_and_push(self, state: _State, message: str) -> None:
        """每個主要階段 commit 後立即推送分支，讓進度（含失敗的 run）在 GitHub 上可見。"""
        self._repo.commit(state["workspace"], message)
        self._repo.push_branch(state["workspace"], state["branch"])

    def _update_pr(self, state: _State, run: FeatureRun, *, body: str, title: str | None = None) -> None:
        if run.pull_request is None or "repo" not in state:
            return
        try:
            run.pull_request = self._repo.update_pull_request(state["repo"], run.pull_request, title=title, body=body)
        except Exception as exc:  # PR 更新失敗不中斷流程
            self._record(run, run.stage, f"更新 PR 失敗：{exc}", level="warning")

    def _fail(self, state: _State, run: FeatureRun, stage: RunStage, message: str) -> None:
        """標記失敗、推送目前分支狀態，並在 Draft PR 留下失敗摘要（維持 Draft，不可合併）。"""
        run.fail(stage, message)
        if "workspace" in state and "branch" in state:
            try:
                self._repo.commit(state["workspace"], f"wip: {stage.value} 階段失敗時的狀態")
                self._repo.push_branch(state["workspace"], state["branch"])
            except Exception as exc:
                run.record(RunStage.FAILED, f"推送失敗狀態時發生錯誤：{exc}", level="warning")
        self._update_pr(
            state,
            run,
            body=self._progress_body(run, f"❌ 失敗於 {stage.value}"),
            title=self._pr_title(state, run, wip=True),
        )
        self._emit(run)

    @staticmethod
    def _pr_title(state: _State, run: FeatureRun, *, wip: bool) -> str:
        base = f"feat: {run.feature_slug or 'feature'} ({state.get('feature_id', '000')})"
        return f"[WIP] {base}" if wip else base

    @staticmethod
    def _progress_body(run: FeatureRun, status: str) -> str:
        checklist = "\n".join(f"- [{'x' if t.done else ' '}] {t.id} {t.title}" for t in run.tasks)
        lines = [
            "## 摘要",
            "由 Java Coding Agent 依 SDD 流程自動實作業務需求：",
            "",
            f"> {run.requirement}",
            "",
            f"**狀態**：{status}（階段：{run.stage.value}）",
        ]
        if run.error:
            lines += ["", "```", run.error[:1500], "```"]
        lines += ["", "## Tasks", checklist or "（尚未拆解）", "", f"Run ID：`{run.id}`"]
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------ helpers
    def _ask_for_files(
        self,
        run: FeatureRun,
        prompt: str,
        *,
        name: str,
        expect_paths_containing: str | None = None,
        workspace: Path | None = None,
        base_package: str | None = None,
        protected_paths: Sequence[str] = (),
        effort: str = "normal",
        allowed_test_modules: Sequence[str] | None = None,
        allow_bdd: bool = True,
    ) -> tuple[str, list[GeneratedFile]]:
        system = self._prompt_store.get_active().content
        feedback = ""
        protected = {p for p in protected_paths if not p.endswith("UseCaseConfiguration.java")}
        for attempt in range(self._opt.max_parse_retries + 1):
            # 第二次以後的重試改用高推理模式（thinking + 三倍輸出額度），避免同樣的截斷/空輸出重複發生
            call_effort = "high" if attempt >= 2 else effort
            text = self._llm.complete(
                system, prompt + feedback, name=name, metadata=self._meta(run), effort=call_effort
            )
            try:
                files = parse_file_blocks(text)
                if workspace is not None:
                    # 自動補上專案內型別的 import（模型最常見的 cannot find symbol 來源）
                    known = {f.path: f.content for f in self._repo.read_files(workspace, ("**/*.java",))}
                    known.update({f.path: f.content for f in files})
                    index = project_type_index(known)
                    shadows = shadowing_violations(files, {k: v for k, v in index.items()})
                    if shadows:
                        raise LlmOutputError("型別重複定義：\n- " + "\n- ".join(shadows[:8]))
                    by_simple = {path.rsplit("/", 1)[-1][:-5]: content for path, content in known.items()}
                    completed: list[GeneratedFile] = []
                    for f in files:
                        content = f.content
                        ctrl = webmvc_controller_name(content) if "/src/test/" in f.path else None
                        if ctrl and ctrl in by_simple:
                            content = complete_webmvc_mocks(content, by_simple[ctrl])
                        completed.append(GeneratedFile(f.path, add_missing_project_imports(f.path, content, index)))
                    # RED 階段寫的測試常引用尚不存在的型別；GREEN 建立這些型別後，回頭幫既有測試補 import
                    touched_modules = {f.path.split("/")[0] for f in files}
                    output_paths = {f.path for f in files}
                    for path, content in known.items():
                        if (
                            "/src/test/" in path
                            and path.endswith(".java")
                            and path not in output_paths
                            and path.split("/")[0] in touched_modules
                        ):
                            fixed = add_missing_project_imports(path, content, index)
                            if fixed != content:
                                completed.append(GeneratedFile(path, fixed))
                    files = completed
                if expect_paths_containing and not any(expect_paths_containing in f.path for f in files):
                    raise LlmOutputError(f"輸出缺少路徑含 `{expect_paths_containing}` 的檔案")
                truncated = truncated_java_files(files)
                if truncated:
                    raise LlmOutputError("以下檔案被截斷：\n- " + "\n- ".join(truncated[:8]))
                gherkin = gherkin_violations(files)
                if gherkin:
                    raise LlmOutputError(".feature 檔有誤：\n- " + "\n- ".join(gherkin[:8]))
                if not allow_bdd:
                    bdd = [f.path for f in files if f.path.endswith(".feature") or "/bdd/" in f.path]
                    if bdd:
                        raise LlmOutputError(
                            "Cucumber 驗收測試（.feature 與 bdd 步驟類別）屬於最後的 BDD 任務；"
                            "本任務的 Controller / 組態尚未全部完成，現在寫會一直失敗。請只輸出本任務的檔案：\n- "
                            + "\n- ".join(bdd)
                        )
                steps = cucumber_step_violations(files)
                if steps:
                    raise LlmOutputError("Cucumber 步驧定義有誤：\n- " + "\n- ".join(steps[:8]))
                pre = cucumber_precondition_violations(files)
                if pre:
                    raise LlmOutputError("Cucumber 前置步驟寫法有誤：\n- " + "\n- ".join(pre[:4]))
                uniq = uniqueness_test_violations(files)
                if uniq:
                    raise LlmOutputError("唯一性測試寫錯：\n- " + "\n- ".join(uniq[:4]))
                if allowed_test_modules is not None and workspace is not None:
                    existing_tests = {f.path for f in self._repo.read_files(workspace, ("**/src/test/**/*.java",))}
                    out_of_scope = [
                        f.path
                        for f in files
                        if "/src/test/" in f.path
                        and f.path.endswith(".java")
                        and f.path not in existing_tests
                        and f.path.split("/")[0] not in allowed_test_modules
                    ]
                    if out_of_scope:
                        raise LlmOutputError(
                            "超出本任務範圍：不要在其他模組新增測試（本任務的模組："
                            + ", ".join(sorted(allowed_test_modules))
                            + "）。請只修正本任務的測試與實作：\n- "
                            + "\n- ".join(out_of_scope)
                        )
                written = {f.path for f in files}
                # 「DELETE 後在同一輸出重建同路徑」等同覆寫，不視為刪除
                deletions = [d for d in parse_deletions(text) if d.path not in written]
                touched = [f.path for f in files if f.path in protected]
                # 骨架檔一律不可刪除（UseCaseConfiguration 可修改但不可刪除或搬移）
                deleted_protected = [d.path for d in deletions if d.path in set(protected_paths)]
                if touched and len(touched) < len(files) and not deleted_protected:
                    # 模型執著於改寫骨架（CucumberSpringConfiguration、ApplicationContextTest…），
                    # 整份輸出被退回只會再重複一次；直接忽略這些檔案、保留其餘輸出即可
                    files = [f for f in files if f.path not in protected]
                    self._record(
                        run,
                        RunStage.IMPLEMENT,
                        "已忽略對骨架檔案的修改（不可修改）：" + ", ".join(p.rsplit("/", 1)[-1] for p in touched),
                        level="warning",
                    )
                    touched = []
                touched += deleted_protected
                if touched:
                    raise LlmOutputError(
                        "以下為系統骨架檔案，不可修改或刪除（只有 bootstrap 的 UseCaseConfiguration.java 可改寫，"
                        "且不可 DELETE）：\n- " + "\n- ".join(touched)
                    )
                if base_package:
                    violations = package_violations(files, base_package)
                    if violations:
                        raise LlmOutputError("套件與模組目錄不一致：\n- " + "\n- ".join(violations[:8]))
                    fw = framework_violations(files)
                    if fw:
                        raise LlmOutputError("domain/application 不得依賴框架：\n- " + "\n- ".join(fw[:8]))
                    pe = persistence_violations(files)
                    if pe:
                        raise LlmOutputError("持久化 adapter 寫法不符合專案慣例：\n- " + "\n- ".join(pe[:8]))
                    pv = path_variable_violations(files)
                    if pv:
                        raise LlmOutputError(
                            "@PathVariable 名稱與路徑變數不一致（執行期會回 500）：\n- " + "\n- ".join(pv[:8])
                        )
                # RED 階段只寫測試：不可刪除既有主程式碼（否則 RED 的失敗其實是編譯被弄壞，GREEN 也要重建被刪的類別）
                if name == "sdd.write_test":
                    existing_main = (
                        {f.path for f in self._repo.read_files(workspace, ("**/src/main/**/*.java",))}
                        if workspace is not None
                        else None
                    )
                    main_deleted = [
                        d.path
                        for d in deletions
                        if "/src/main/" in d.path and (existing_main is None or d.path in existing_main)
                    ]
                    # 刪除不存在的路徑（模型幻想出的子套件）不算破壞，直接忽略
                    deletions = [
                        d
                        for d in deletions
                        if existing_main is None or "/src/main/" not in d.path or d.path in existing_main
                    ]
                    if main_deleted:
                        raise LlmOutputError(
                            "RED 階段只能新增/修改測試，不可刪除主程式碼檔案（需要調整實作請留到 GREEN）：\n- "
                            + "\n- ".join(main_deleted)
                        )
                # TDD 紀律：不可用「刪除測試」讓建置通過。刪除 src/test 檔案只允許搬移（同名檔案在輸出中重建）。
                recreated = {f.path.rsplit("/", 1)[-1] for f in files}
                existing_tests_all = (
                    {
                        f.path: f.content
                        for f in self._repo.read_files(workspace, ("**/src/test/**/*.java", "**/src/test/**/*.feature"))
                    }
                    if workspace is not None
                    else None
                )
                # 刪除不存在的測試路徑（模型幻想出的子套件，如 entity/XTest.java）不算破壞，直接忽略；
                # 刪除放錯到 src/test 的主程式類別（沒有任何測試註解，如 bdd/GlobalExceptionHandler.java）也允許
                illegal = [
                    d.path
                    for d in deletions
                    if "/src/test/" in d.path
                    and d.path.endswith((".java", ".feature"))
                    and d.path.rsplit("/", 1)[-1] not in recreated
                    and (
                        existing_tests_all is None
                        or (d.path in existing_tests_all and _looks_like_test(d.path, existing_tests_all[d.path]))
                    )
                ]
                deletions = [
                    d
                    for d in deletions
                    if existing_tests_all is None or "/src/test/" not in d.path or d.path in existing_tests_all
                ]
                if illegal:
                    raise LlmOutputError(
                        "不可刪除測試檔案來讓建置通過（只能修正測試或實作；搬移時須在同一輸出重建同名檔案）：\n- "
                        + "\n- ".join(illegal)
                    )
                if workspace is not None:
                    existing_files = self._repo.read_files(workspace, ("**/*.java",))
                    existing = {f.path for f in existing_files}
                    resulting = (existing - {d.path for d in deletions}) | {f.path for f in files}
                    duplicates = duplicate_simple_names(resulting)
                    if duplicates:
                        raise LlmOutputError(
                            "同名型別會造成 ambiguous reference，請重新命名（inbound port 以 UseCase 結尾、"
                            "outbound port 以 Port 結尾）或以 DELETE FILE 刪除多餘檔案：\n- " + "\n- ".join(duplicates)
                        )
                    if name in ("sdd.write_impl", "sdd.fix"):
                        stubs = stub_violations(files)
                        if stubs:
                            raise LlmOutputError(
                                "主程式碼不可留下『尚未實作』佔位（UnsupportedOperationException / TODO）；"
                                "請直接寫出完整實作：\n- " + "\n- ".join(stubs)
                            )
                        deleted = {d.path for d in deletions}
                        sources = {f.path: f.content for f in existing_files if f.path not in deleted}
                        sources.update({f.path: f.content for f in files})
                        missing_impl = unimplemented_use_cases(sources)
                        if missing_impl:
                            raise LlmOutputError(
                                "以下 inbound port（UseCase 介面）尚無任何實作類別，請在同一輸出中於 "
                                "application/.../application/usecase/ 補上對應的 `*Service implements *UseCase`"
                                "（不可只實作其中一個 use case）：\n- " + "\n- ".join(missing_impl)
                            )
                if deletions and workspace is not None:
                    self._repo.delete_files(workspace, deletions)
                    self._record(
                        run, run.stage, f"依 LLM 指示刪除 {len(deletions)} 個檔案", paths=[d.path for d in deletions]
                    )
                return text, files
            except (LlmOutputError, DomainError) as exc:
                feedback = (
                    f"\n\n上一次輸出有問題：{exc}。請**立刻**以『=== FILE: path ===』格式輸出完整檔案，"
                    "不要分析、不要自問自答、不要任何前言；檔案區塊之後最多 3 行說明。"
                )
                self._record(
                    run,
                    run.stage,
                    f"LLM 輸出解析失敗（第 {attempt + 1} 次）：{exc}",
                    level="warning",
                    output_head=text[:600],
                    output_tail=text[-400:],
                    output_chars=len(text),
                )
        raise LlmOutputError(f"{name} 多次輸出皆無法解析")

    def _task_context(self, state: _State, task: TaskItem) -> dict[str, Any]:
        run = state["run"]
        # UseCaseConfiguration 是唯一允許修改的骨架檔，必須讓 LLM 看到目前內容
        editable_scaffold = {p for p in state["scaffold_paths"] if p.endswith("UseCaseConfiguration.java")}
        excluded = set(state["scaffold_paths"]) - editable_scaffold
        existing = [
            f
            for f in self._repo.read_files(state["workspace"], _SOURCE_PATTERNS)
            if f.path not in excluded and "/build/" not in f.path
        ]
        return {
            "task": task,
            "plan_md": run.plan_md,
            "existing_files": render_file_blocks(existing) if existing else "（尚無）",
            "scaffold_paths": "\n".join(
                f"- {p}{'（可修改，內容見上方既有檔案）' if p in editable_scaffold else ''}"
                for p in state["scaffold_paths"]
            ),
            "base_package": state["base_package"],
            "base_package_path": package_path(state["base_package"]),
        }

    def _system_prompt(self, state: _State) -> str:
        content = self._prompt_store.get_active().content
        return content.replace("{base_package}", state.get("base_package", "")).replace(
            "{base_package_path}", package_path(state.get("base_package", ""))
        )

    @staticmethod
    def _ensure_checklist(body: str, tasks: Sequence[TaskItem], requirement: str) -> str:
        checklist = "\n".join(f"- [x] {t.id} {t.title}" for t in tasks)
        if "- [x]" in body:
            return body.strip() + "\n"
        header = body.strip() or f"## 摘要\n依 SDD 流程實作業務需求：\n\n> {requirement}\n"
        return f"{header}\n\n## Tasks\n{checklist}\n"

    def _record(self, run: FeatureRun, stage: RunStage, message: str, level: str = "info", **data: Any) -> None:
        run.record(stage, message, level=level, **data)
        self._emit(run)

    def _emit(self, run: FeatureRun) -> None:
        self._on_event(run)

    @staticmethod
    def _meta(run: FeatureRun) -> dict[str, Any]:
        return {"run_id": run.id, "stage": run.stage.value, "feature": run.feature_slug}


_MODULE_IN_TEXT = re.compile(r"\b(domain|application|adapter-web|adapter-persistence|bootstrap)/src/")


def _modules_in_text(text: str) -> set[str]:
    """從 tasks.md 的說明/測試欄位抓出提到的模組目錄，用來限制 RED 階段新增測試的範圍。"""
    return set(_MODULE_IN_TEXT.findall(text))


_CONCRETE_CLASS = re.compile(
    r"^\s*(?:public\s+)?(?:final\s+)?class\s+\w+|@(?:RestController|Controller|Entity|Component)\b", re.M
)


def _needs_tests(files: Sequence[GeneratedFile]) -> bool:
    """輸出中若有具體類別（非 interface / record / enum），該任務就必須有測試。"""
    return any(f.path.endswith(".java") and "/src/main/" in f.path and _CONCRETE_CLASS.search(f.content) for f in files)


StageLiteral = Literal["prepare", "specify", "plan", "tasks", "implement_task", "verify", "publish"]
