"""US-3 / US-4：LLM 設定與目標 repo 設定。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from java_coding_agent.domain.models import LlmSettings, RepoSettings
from java_coding_agent.domain.ports import SettingsRepository


class LlmSettingsService:
    def __init__(self, repository: SettingsRepository, on_change: Callable[[LlmSettings], None] | None = None):
        self._repo = repository
        self._on_change = on_change

    def current(self) -> LlmSettings:
        return self._repo.load_llm_settings()

    def update(self, **changes: Any) -> LlmSettings:
        cleaned = {k: v for k, v in changes.items() if v is not None}
        updated = self.current().updated(**cleaned)
        self._repo.save_llm_settings(updated)
        if self._on_change:
            self._on_change(updated)
        return updated


class RepoSettingsService:
    def __init__(self, repository: SettingsRepository) -> None:
        self._repo = repository

    def current(self) -> RepoSettings:
        return self._repo.load_repo_settings()

    def update(self, url: str | None = None, base_branch: str | None = None) -> RepoSettings:
        changes = {k: v for k, v in {"url": url, "base_branch": base_branch}.items() if v}
        updated = self.current().updated(**changes)
        self._repo.save_repo_settings(updated)
        return updated
