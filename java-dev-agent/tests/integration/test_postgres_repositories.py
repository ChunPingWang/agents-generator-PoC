"""整合測試：以 Testcontainers 啟動 PostgreSQL，驗證 SQLAlchemy repositories。需要 Docker。"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

testcontainers = pytest.importorskip("testcontainers.postgres")

from java_coding_agent.adapters.outbound.persistence.repositories import (  # noqa: E402
    SqlConversationRepository,
    SqlRunRepository,
    SqlSettingsRepository,
    make_engine,
)
from java_coding_agent.domain.models import (  # noqa: E402
    Conversation,
    FeatureRun,
    LlmProvider,
    LlmSettings,
    PullRequest,
    RepoSettings,
    Role,
    RunStage,
)


@pytest.fixture(scope="module")
def engine():
    with testcontainers.PostgresContainer("postgres:16-alpine", driver="psycopg") as pg:
        yield make_engine(pg.get_connection_url())


def test_settings_roundtrip_on_postgres(engine):
    repo = SqlSettingsRepository(engine, LlmSettings(), RepoSettings())
    repo.save_llm_settings(LlmSettings(provider=LlmProvider.ANTHROPIC, model="claude", context_length=200000))
    loaded = repo.load_llm_settings()
    assert (loaded.provider, loaded.context_length) == (LlmProvider.ANTHROPIC, 200000)


def test_conversation_and_run_roundtrip_on_postgres(engine):
    conversations = SqlConversationRepository(engine)
    c = Conversation("pg-c1")
    c.add(Role.USER, "你好")
    conversations.save(c)
    assert conversations.get("pg-c1").messages[0].content == "你好"

    runs = SqlRunRepository(engine)
    run = FeatureRun("pg-r1", "需求")
    run.record(RunStage.SPECIFY, "開始")
    runs.save(run)
    run.succeed(PullRequest("https://github.com/a/b/pull/3", 3, "feature/x"))
    runs.save(run)
    reloaded = runs.get("pg-r1")
    assert reloaded is not run  # 終態後從 DB 反序列化
    assert reloaded.stage is RunStage.SUCCEEDED and reloaded.pull_request.number == 3
    assert [r.id for r in runs.list_recent()] == ["pg-r1"]
