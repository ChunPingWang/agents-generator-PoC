"""SettingsRepository / ConversationRepository / RunRepository 的 SQLAlchemy 實作。"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from java_coding_agent.adapters.outbound.persistence import serialization as ser
from java_coding_agent.adapters.outbound.persistence.models import Base, ConversationRow, RunRow, SettingRow
from java_coding_agent.domain.models import Conversation, FeatureRun, LlmSettings, RepoSettings, RunStage


def make_engine(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, future=True, connect_args=connect_args, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    return engine


class SqlSettingsRepository:
    def __init__(self, engine: Engine, default_llm: LlmSettings, default_repo: RepoSettings) -> None:
        self._engine = engine
        self._default_llm = default_llm
        self._default_repo = default_repo

    def _get(self, key: str) -> dict | None:
        with Session(self._engine) as s:
            row = s.get(SettingRow, key)
            return dict(row.value) if row else None

    def _put(self, key: str, value: dict) -> None:
        with Session(self._engine) as s:
            row = s.get(SettingRow, key)
            if row:
                row.value = value
            else:
                s.add(SettingRow(key=key, value=value))
            s.commit()

    def load_llm_settings(self) -> LlmSettings:
        raw = self._get("llm")
        return ser.llm_settings_from_dict(raw) if raw else self._default_llm

    def save_llm_settings(self, settings: LlmSettings) -> None:
        self._put("llm", ser.llm_settings_to_dict(settings))

    def load_repo_settings(self) -> RepoSettings:
        raw = self._get("repo")
        return ser.repo_settings_from_dict(raw) if raw else self._default_repo

    def save_repo_settings(self, settings: RepoSettings) -> None:
        self._put("repo", ser.repo_settings_to_dict(settings))


class SqlConversationRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, conversation_id: str) -> Conversation | None:
        with Session(self._engine) as s:
            row = s.get(ConversationRow, conversation_id)
            return ser.conversation_from_rows(row.id, list(row.messages)) if row else None

    def save(self, conversation: Conversation) -> None:
        with Session(self._engine) as s:
            row = s.get(ConversationRow, conversation.id)
            payload = ser.conversation_to_rows(conversation)
            if row:
                row.messages = payload
            else:
                s.add(ConversationRow(id=conversation.id, messages=payload))
            s.commit()


class SqlRunRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._live: dict[str, FeatureRun] = {}
        """執行中的 Run 物件以記憶體為準（避免反序列化造成的物件分叉），DB 作為持久化副本。"""

    def get(self, run_id: str) -> FeatureRun | None:
        if run_id in self._live:
            return self._live[run_id]
        with Session(self._engine) as s:
            row = s.get(RunRow, run_id)
            return ser.run_from_dict(dict(row.payload)) if row else None

    def save(self, run: FeatureRun) -> None:
        if run.stage.is_terminal:
            self._live.pop(run.id, None)
        else:
            self._live[run.id] = run
        with Session(self._engine) as s:
            row = s.get(RunRow, run.id)
            payload = ser.run_to_dict(run)
            if row:
                row.stage, row.payload = run.stage.value, payload
            else:
                s.add(
                    RunRow(
                        id=run.id,
                        stage=run.stage.value,
                        requirement=run.requirement,
                        created_at=run.created_at,
                        payload=payload,
                    )
                )
            s.commit()

    def list_recent(self, limit: int = 20) -> Sequence[FeatureRun]:
        with Session(self._engine) as s:
            rows = s.execute(select(RunRow).order_by(RunRow.created_at.desc()).limit(limit)).scalars().all()
            return [self._live.get(r.id) or ser.run_from_dict(dict(r.payload)) for r in rows]

    def fail_stale_runs(self, reason: str) -> int:
        """啟動時呼叫：資料庫中仍為非終態的 Run 代表上個程序中斷，標記為失敗。"""
        terminal = {RunStage.SUCCEEDED.value, RunStage.FAILED.value}
        count = 0
        with Session(self._engine) as s:
            for row in s.execute(select(RunRow).where(RunRow.stage.notin_(terminal))).scalars().all():
                run = ser.run_from_dict(dict(row.payload))
                run.fail(run.stage, reason)
                row.stage, row.payload = run.stage.value, ser.run_to_dict(run)
                count += 1
            s.commit()
        return count
