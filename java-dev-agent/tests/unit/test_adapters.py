"""Outbound adapters 的單元測試：不需 Docker / 網路 / 真實 LLM。"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from git import Repo

from java_coding_agent.adapters.outbound.build.gradle_runner import GradleBuildRunner
from java_coding_agent.adapters.outbound.github.git_github_repository import GitGitHubRepository
from java_coding_agent.adapters.outbound.llm.model_factory import LangChainModelFactory
from java_coding_agent.adapters.outbound.persistence.repositories import (
    SqlConversationRepository,
    SqlRunRepository,
    SqlSettingsRepository,
    make_engine,
)
from java_coding_agent.adapters.outbound.prompt_store.file_prompt_store import FilePromptStore
from java_coding_agent.adapters.outbound.scaffold.jinja_scaffolder import JinjaScaffolder
from java_coding_agent.domain.errors import ValidationError
from java_coding_agent.domain.models import (
    Conversation,
    FeatureRun,
    GeneratedFile,
    LlmProvider,
    LlmSettings,
    PullRequest,
    RepoSettings,
    Role,
    RunStage,
)


# ----------------------------------------------------------------- FilePromptStore
class TestFilePromptStore:
    def test_initializes_with_default_and_versions(self, tmp_path):
        store = FilePromptStore(tmp_path / "p.json", "DEFAULT")
        assert store.get_active().content == "DEFAULT" and store.get_active().is_default
        v2 = store.save_new_version("NEW", "note")
        assert (v2.version, store.get_active().content) == (2, "NEW")
        v3 = store.reset_to_default()
        assert v3.is_default and store.get_active().content == "DEFAULT"
        assert [v.version for v in store.history()] == [1, 2, 3]

    def test_persists_across_instances(self, tmp_path):
        FilePromptStore(tmp_path / "p.json", "D").save_new_version("X", "n")
        assert FilePromptStore(tmp_path / "p.json", "D").get_active().content == "X"


# ----------------------------------------------------------------- SQL repositories (SQLite)
@pytest.fixture
def engine(tmp_path):
    return make_engine(f"sqlite:///{tmp_path / 't.db'}")


class TestSqlRepositories:
    def test_settings_defaults_then_roundtrip(self, engine):
        repo = SqlSettingsRepository(engine, LlmSettings(), RepoSettings())
        assert repo.load_llm_settings() == LlmSettings()
        repo.save_llm_settings(LlmSettings(provider=LlmProvider.OPENAI, model="gpt", temperature=0.3))
        loaded = repo.load_llm_settings()
        assert (loaded.provider, loaded.model, loaded.temperature) == (LlmProvider.OPENAI, "gpt", 0.3)
        repo.save_repo_settings(RepoSettings(url="https://github.com/a/b", base_branch="dev"))
        assert repo.load_repo_settings().full_name == "a/b"

    def test_conversation_roundtrip(self, engine):
        repo = SqlConversationRepository(engine)
        c = Conversation("c1")
        c.add(Role.USER, "hi")
        c.add(Role.ASSISTANT, "你好")
        repo.save(c)
        loaded = repo.get("c1")
        assert [(m.role, m.content) for m in loaded.messages] == [(Role.USER, "hi"), (Role.ASSISTANT, "你好")]
        assert repo.get("missing") is None

    def test_run_roundtrip_and_recent_order(self, engine):
        repo = SqlRunRepository(engine)
        r1 = FeatureRun("r1", "需求一")
        r1.record(RunStage.SPECIFY, "開始")
        repo.save(r1)
        r1.succeed(PullRequest("https://github.com/a/b/pull/1", 1, "feature/x"))
        repo.save(r1)
        r2 = FeatureRun("r2", "需求二")
        repo.save(r2)
        assert repo.get("r1").pull_request.number == 1
        assert repo.get("r1").events[0].message == "開始"
        assert [r.id for r in repo.list_recent()] == ["r2", "r1"]
        # 執行中的 run 回傳同一物件（避免分叉）
        assert repo.get("r2") is r2


# ----------------------------------------------------------------- GradleBuildRunner (fake gradlew)
def _write_fake_gradlew(workspace: Path, exit_code: int, output: str) -> None:
    script = workspace / "gradlew"
    script.write_text(f"#!/bin/sh\necho '{output}'\necho ARGS=$@\nexit {exit_code}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


class TestGradleBuildRunner:
    def test_success_and_failure_are_reported(self, tmp_path):
        _write_fake_gradlew(tmp_path, 0, "BUILD SUCCESSFUL")
        ok = GradleBuildRunner(java_home="/fake/jdk").run_tests(tmp_path)
        assert ok.success and "BUILD SUCCESSFUL" in ok.stdout and "test" in ok.stdout
        _write_fake_gradlew(tmp_path, 1, "FooTest FAILED")
        bad = GradleBuildRunner().run_tests(tmp_path, test_filter="*FooTest")
        assert not bad.success and bad.exit_code == 1 and "--tests *FooTest" in bad.stdout
        assert "FooTest FAILED" in bad.summary()

    def test_java_home_is_passed_to_subprocess(self, tmp_path):
        (tmp_path / "gradlew").write_text("#!/bin/sh\necho JAVA_HOME=$JAVA_HOME\n")
        os.chmod(tmp_path / "gradlew", 0o755)
        res = GradleBuildRunner(java_home="/opt/jdk17").run_tests(tmp_path)
        assert "JAVA_HOME=/opt/jdk17" in res.stdout

    def test_generates_wrapper_when_missing(self, tmp_path):
        fake_gradle = tmp_path / "bin" / "gradle"
        fake_gradle.parent.mkdir()
        fake_gradle.write_text("#!/bin/sh\nprintf '#!/bin/sh\\necho WRAPPED\\n' > gradlew\nchmod +x gradlew\n")
        fake_gradle.chmod(0o755)
        res = GradleBuildRunner(gradle_bin=str(fake_gradle)).run_tests(tmp_path)
        assert res.success and "WRAPPED" in res.stdout

    def test_relative_workspace_path_is_resolved(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ws = Path("workspace/runs/r1")
        ws.mkdir(parents=True)
        _write_fake_gradlew(ws, 0, "OK")
        assert GradleBuildRunner().run_tests(ws).success

    def test_timeout(self, tmp_path):
        (tmp_path / "gradlew").write_text("#!/bin/sh\nsleep 5\n")
        os.chmod(tmp_path / "gradlew", 0o755)
        res = GradleBuildRunner(timeout_seconds=1).run_tests(tmp_path)
        assert not res.success and res.exit_code == 124


# ----------------------------------------------------------------- JinjaScaffolder
class TestJinjaScaffolder:
    def test_renders_all_modules_and_package_paths(self):
        files = JinjaScaffolder().scaffold("profile-service", "com.acme.profile", "001")
        paths = {f.path for f in files}
        assert "settings.gradle.kts" in paths and ".gitignore" in paths
        for module in ("domain", "application", "adapter-web", "adapter-persistence", "bootstrap"):
            assert f"{module}/build.gradle.kts" in paths
        assert "bootstrap/src/main/java/com/acme/profile/Application.java" in paths
        assert "adapter-web/src/test/java/com/acme/profile/adapter/web/WebTestApplication.java" in paths
        assert "adapter-persistence/src/test/resources/application.yml" in paths
        assert "bootstrap/src/test/java/com/acme/profile/bootstrap/ApplicationContextTest.java" in paths
        arch = next(f for f in files if f.path.endswith("ArchitectureTest.java"))
        assert 'BASE = "com.acme.profile"' in arch.content
        settings = next(f for f in files if f.path == "settings.gradle.kts")
        assert 'rootProject.name = "profile-service"' in settings.content
        assert not any("{{" in f.content for f in files)

    def test_fail_on_no_discovered_tests_disabled_for_tdd_start(self):
        root = next(f for f in JinjaScaffolder().scaffold("x", "a.b", "001") if f.path == "build.gradle.kts")
        assert "failOnNoDiscoveredTests = false" in root.content

    def test_test_tasks_have_a_timeout_against_hung_test_jvms(self):
        root = next(f for f in JinjaScaffolder().scaffold("x", "a.b", "001") if f.path == "build.gradle.kts")
        # `java.time.Duration` 在 subprojects 區塊內會被解析成 java 外掛的 extension，必須先 import
        assert root.content.startswith("import java.time.Duration\n")
        assert "timeout.set(Duration.ofMinutes(10))" in root.content


# ----------------------------------------------------------------- GitGitHubRepository (local bare remote)
class FakePrClient:
    def __init__(self):
        self.calls = []
        self.updates = []

    def create_pull(self, full_name, *, title, body, head, base, draft=False):
        self.calls.append((full_name, title, body, head, base))
        return PullRequest(url=f"https://github.com/{full_name}/pull/7", number=7, branch=head, draft=draft)

    def update_pull(self, full_name, number, *, title=None, body=None, ready_for_review=False):
        self.updates.append((number, title, body, ready_for_review))
        return PullRequest(url=f"https://github.com/{full_name}/pull/{number}", number=number, branch="b", draft=False)


@pytest.fixture
def bare_remote(tmp_path):
    remote = tmp_path / "remote.git"
    Repo.init(remote, bare=True)
    return remote


class TestGitGitHubRepository:
    def _adapter(self, remote: Path) -> GitGitHubRepository:
        adapter = GitGitHubRepository(token="dummy", pr_client=FakePrClient())
        adapter._authed_url = lambda repo: str(remote)  # type: ignore[method-assign]  # 指向本機 bare repo
        return adapter

    def test_empty_remote_gets_base_branch_then_feature_branch(self, tmp_path, bare_remote):
        adapter = self._adapter(bare_remote)
        settings = RepoSettings(url="https://github.com/acme/svc.git", base_branch="main")
        ws = adapter.prepare_workspace(settings, "feature/abc", tmp_path / "ws")
        repo = Repo(ws)
        assert repo.active_branch.name == "feature/abc"
        assert "main" in [r.remote_head for r in repo.remotes.origin.refs]

    def test_write_commit_push_and_pr(self, tmp_path, bare_remote):
        adapter = self._adapter(bare_remote)
        settings = RepoSettings(url="https://github.com/acme/svc.git")
        ws = adapter.prepare_workspace(settings, "feature/abc", tmp_path / "ws")
        adapter.write_files(ws, [GeneratedFile("src/A.java", "class A {}\n"), GeneratedFile("build/x.txt", "ignored")])
        sha = adapter.commit(ws, "feat: A")
        assert sha and adapter.commit(ws, "nothing") == ""
        adapter.push_branch(ws, "feature/abc")
        assert "feature/abc" in [h.name for h in Repo(bare_remote).heads]
        pr = adapter.open_pull_request(settings, "feature/abc", "title", "body", draft=True)
        assert pr.number == 7 and pr.draft
        assert adapter._pr_client.calls[0][3:] == ("feature/abc", "main")
        ready = adapter.update_pull_request(settings, pr, body="final", ready_for_review=True)
        assert not ready.draft and adapter._pr_client.updates == [(7, None, "final", True)]

    def test_commit_force_adds_sources_hidden_by_gitignore(self, tmp_path, bare_remote):
        adapter = self._adapter(bare_remote)
        ws = adapter.prepare_workspace(RepoSettings(url="https://github.com/acme/svc.git"), "f", tmp_path / "ws")
        adapter.write_files(
            ws,
            [
                GeneratedFile(".gitignore", "build/\nout/\n"),
                GeneratedFile(
                    "application/src/main/java/a/application/port/out/SavePort.java", "interface SavePort {}\n"
                ),
                GeneratedFile("application/build/junk.txt", "x"),
            ],
        )
        assert adapter.commit(ws, "feat: ports")
        tracked = set(Repo(ws).git.ls_files().splitlines())
        assert "application/src/main/java/a/application/port/out/SavePort.java" in tracked
        assert "application/build/junk.txt" not in tracked

    def test_read_files_filters_by_pattern_and_excludes_build_dirs(self, tmp_path, bare_remote):
        adapter = self._adapter(bare_remote)
        ws = adapter.prepare_workspace(RepoSettings(url="https://github.com/acme/svc.git"), "f", tmp_path / "ws")
        adapter.write_files(
            ws,
            [
                GeneratedFile("domain/src/main/java/A.java", "a"),
                GeneratedFile("domain/build/B.java", "b"),
                GeneratedFile("specs/001-x/spec.md", "s"),
                GeneratedFile("README.md", "r"),
            ],
        )
        found = {f.path for f in adapter.read_files(ws, ("**/*.java", "specs/**/*.md"))}
        assert found == {"domain/src/main/java/A.java", "specs/001-x/spec.md"}

    def test_requires_token(self, monkeypatch):
        monkeypatch.setattr(
            "java_coding_agent.adapters.outbound.github.git_github_repository._gh_cli_token", lambda: None
        )
        with pytest.raises(RuntimeError):
            GitGitHubRepository(token=None)


# ----------------------------------------------------------------- Model factory
class TestModelFactory:
    def test_ollama_model_gets_context_and_seed(self):
        model = LangChainModelFactory().create(LlmSettings(model="qwen3", context_length=4096, seed=7))
        assert type(model).__name__ == "ChatOllama"
        assert model.num_ctx == 4096 and model.seed == 7 and model.temperature == 0.0
        assert model.reasoning is False and model.num_predict == 24000
        assert LangChainModelFactory(thinking=True).create(LlmSettings()).reasoning is True
        high = LangChainModelFactory().create(LlmSettings(), effort="high")
        assert high.reasoning is True and high.num_predict == 72000

    def test_openai_requires_api_key_env(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ValidationError):
            LangChainModelFactory().create(
                LlmSettings(provider=LlmProvider.OPENAI, model="gpt-x", base_url="https://api.openai.com/v1")
            )

    def test_openai_with_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        model = LangChainModelFactory().create(
            LlmSettings(provider=LlmProvider.OPENAI, model="gpt-x", base_url="http://localhost:1234/v1")
        )
        assert type(model).__name__ == "ChatOpenAI"


def test_git_adapter_deletes_files(tmp_path):
    from java_coding_agent.domain.models import FileDeletion

    adapter = GitGitHubRepository(token="dummy", pr_client=FakePrClient())
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "B.java").write_text("x")
    adapter.delete_files(tmp_path, [FileDeletion("a/B.java"), FileDeletion("missing.java")])
    assert not (tmp_path / "a" / "B.java").exists()


def test_fail_stale_runs_marks_non_terminal_runs(engine):
    repo = SqlRunRepository(engine)
    running = FeatureRun("stale", "x")
    running.record(RunStage.IMPLEMENT, "working")
    repo.save(running)
    done = FeatureRun("done", "y")
    done.succeed(PullRequest("https://x/pull/1", 1, "f"))
    repo.save(done)
    fresh = SqlRunRepository(engine)  # 模擬新程序
    assert fresh.fail_stale_runs("伺服器重新啟動") == 1
    assert fresh.get("stale").stage is RunStage.FAILED and "重新啟動" in fresh.get("stale").error
    assert fresh.get("done").stage is RunStage.SUCCEEDED


def test_langchain_llm_call_watchdog_raises():
    import time

    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from java_coding_agent.adapters.outbound.llm.langchain_llm import LangChainLlm
    from java_coding_agent.application.run_registry import _NoTracing
    from java_coding_agent.domain.errors import LlmOutputError
    from tests.unit.fakes import InMemorySettings

    class SlowModel(FakeListChatModel):
        def _generate(self, *a, **k):
            time.sleep(2)
            return super()._generate(*a, **k)

    class Factory:
        def create(self, settings, *, effort="normal"):
            return SlowModel(responses=["late"])

    llm = LangChainLlm(Factory(), InMemorySettings(), _NoTracing(), call_timeout_seconds=0.3)
    with pytest.raises(LlmOutputError):
        llm.complete("s", "u", name="t")
    fast = LangChainLlm(
        type("F", (), {"create": lambda self, s, effort="normal": FakeListChatModel(responses=["ok"])})(),
        InMemorySettings(),
        _NoTracing(),
        call_timeout_seconds=5,
    )
    assert fast.complete("s", "u") == "ok"
