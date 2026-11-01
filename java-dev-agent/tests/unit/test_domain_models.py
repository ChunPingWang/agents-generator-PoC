import pytest

from java_coding_agent.domain.errors import ValidationError
from java_coding_agent.domain.models import (
    BuildResult,
    FeatureRun,
    GeneratedFile,
    LlmProvider,
    LlmSettings,
    PullRequest,
    RepoSettings,
    RunStage,
    slugify,
)


class TestLlmSettings:
    def test_default_is_local_ollama_qwen(self):
        s = LlmSettings()
        assert s.provider is LlmProvider.OLLAMA
        assert s.base_url == "http://localhost:11434"
        assert "qwen" in s.model

    def test_updated_accepts_provider_string(self):
        s = LlmSettings().updated(provider="openai", model="gpt-x")
        assert s.provider is LlmProvider.OPENAI
        assert s.model == "gpt-x"

    @pytest.mark.parametrize("bad", [{"temperature": 2.5}, {"context_length": 10}, {"model": " "}, {"provider": "foo"}])
    def test_rejects_invalid_values(self, bad):
        with pytest.raises(ValidationError):
            LlmSettings().updated(**bad)


class TestRepoSettings:
    def test_parses_owner_and_name_from_https(self):
        r = RepoSettings(url="https://github.com/ChunPingWang/ollama-agent-outcome.git")
        assert (r.owner, r.name) == ("ChunPingWang", "ollama-agent-outcome")
        assert r.full_name == "ChunPingWang/ollama-agent-outcome"

    def test_parses_ssh_url_without_git_suffix(self):
        r = RepoSettings(url="git@github.com:acme/svc")
        assert r.full_name == "acme/svc"

    def test_rejects_non_github_url(self):
        with pytest.raises(ValidationError):
            RepoSettings(url="https://gitlab.com/a/b.git")


class TestGeneratedFile:
    def test_normalizes_backslashes(self):
        assert GeneratedFile(path="a\\b.java", content="x").path == "a/b.java"

    @pytest.mark.parametrize("path", ["/etc/passwd", "../x.java", "a/../../b"])
    def test_rejects_escaping_paths(self, path):
        with pytest.raises(ValidationError):
            GeneratedFile(path=path, content="")


class TestBuildResult:
    def test_summary_prefers_error_lines(self):
        out = "\n".join(["ok line"] * 50 + ["FooTest > bar FAILED", "    expected: 1 but was: 2"])
        assert BuildResult(False, 1, stdout=out).summary() == "FooTest > bar FAILED\n    expected: 1 but was: 2"

    def test_summary_falls_back_to_tail(self):
        assert BuildResult(True, 0, stdout="all good").summary() == "all good"

    def test_summary_drops_stack_frames_and_keeps_messages(self):
        out = "\n".join(
            [
                "ProfileJpaRepositoryTest > 應能成功儲存 FAILED",
                "    org.springframework.orm.ObjectOptimisticLockingFailureException: Row was already updated",
                *["        at app//org.springframework.orm.jpa.hibernate.HibernateExceptionTranslator.x(X.java:1)"]
                * 300,
                "        Caused by:",
                "        org.hibernate.StaleObjectStateException: Row was already updated",
                "        ... 95 more",
                "4 tests completed, 3 failed",
            ]
        )
        summary = BuildResult(False, 1, stdout=out).summary()
        assert "at app//" not in summary and "... 95 more" not in summary
        assert summary.splitlines()[0].endswith("FAILED")
        assert "ObjectOptimisticLockingFailureException" in summary
        assert "StaleObjectStateException" in summary and "4 tests completed, 3 failed" in summary

    def test_summary_truncates_long_lines_but_keeps_cause_chain(self):
        out = "\n".join(
            [
                "X > y() FAILED",
                "    java.lang.IllegalStateException: Failed to load ApplicationContext for "
                + "[MergedContextConfiguration "
                + "a" * 3000
                + "]",
                "        Caused by: org.springframework.beans.factory.BeanCreationException: dataSource",
                "        Caused by: DataSourceBeanCreationException: Failed to determine a suitable driver class",
            ]
        )
        summary = BuildResult(False, 1, stdout=out).summary(max_chars=1500)
        assert "Failed to determine a suitable driver class" in summary
        assert len(summary.splitlines()[1]) <= 405

    def test_summary_keeps_message_after_bare_caused_by(self):
        out = (
            "X > y() FAILED\n    java.lang.IllegalStateException: Failed to load ApplicationContext\n"
            "        Caused by:\n"
            "        org.springframework.beans.factory.NoSuchBeanDefinitionException: No qualifying bean of type 'Foo'"
        )
        summary = BuildResult(False, 1, stdout=out).summary()
        assert "No qualifying bean of type 'Foo'" in summary

    def test_summary_keeps_javac_error_context(self):
        out = (
            "Foo.java:12: error: cannot find symbol\n    @Import(X.class)\n     ^\n"
            "  symbol: class Import\n  location: class Foo\n1 error"
        )
        summary = BuildResult(False, 1, stderr=out).summary()
        assert "cannot find symbol" in summary and "@Import(X.class)" in summary and "^" in summary
        assert "symbol: class Import" in summary and "location: class Foo" in summary


class TestBuildResultCucumber:
    def test_summary_keeps_undefined_step_snippets(self):
        out = (
            "成功刪除個人資料 FAILED\n"
            "    io.cucumber.junit.platform.engine.UndefinedStepException: "
            "The step '存在一個 ID 為 \"x\" 的個人資料' is undefined.\n"
            "    You can implement this step using the snippet(s) below:\n\n"
            '    @假設("存在一個 ID 為 {string} 的個人資料")\n'
            "    public void 存在一個_id_為_的個人資料(String string) {\n"
            "        // Write code here that turns the phrase above into concrete actions\n"
            "        throw new io.cucumber.java.PendingException();\n    }\n"
        )
        summary = BuildResult(False, 1, stdout=out).summary()
        assert '@假設("存在一個 ID 為 {string} 的個人資料")' in summary
        assert "is undefined" in summary


class TestFeatureRun:
    def test_record_moves_stage_and_appends_event(self):
        run = FeatureRun(id="r1", requirement="x")
        run.record(RunStage.SPECIFY, "開始")
        assert run.stage is RunStage.SPECIFY
        assert run.events[-1].message == "開始"

    def test_fail_is_terminal_and_keeps_failed_stage(self):
        run = FeatureRun(id="r1", requirement="x")
        run.fail(RunStage.VERIFY, "tests failed")
        assert run.stage.is_terminal
        assert run.error == "[verify] tests failed"
        assert run.events[-1].data["failed_stage"] == "verify"

    def test_succeed_stores_pr(self):
        run = FeatureRun(id="r1", requirement="x")
        run.succeed(PullRequest(url="https://github.com/a/b/pull/1", number=1, branch="feature/x"))
        assert run.stage is RunStage.SUCCEEDED
        assert run.pull_request.number == 1


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Member Discount Rule", "member-discount-rule"), ("個人資料服務", "feature"), ("  A  b ", "a-b")],
)
def test_slugify(text, expected):
    assert slugify(text) == expected
