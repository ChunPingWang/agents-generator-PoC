"""Outbound ports。adapters 以結構型別（Protocol）實作，application 僅依賴這些介面。"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, Protocol

from java_coding_agent.domain.models import (
    BuildResult,
    Conversation,
    FeatureRun,
    FileDeletion,
    GeneratedFile,
    LlmSettings,
    PullRequest,
    RepoSettings,
    SystemPromptVersion,
)


class LlmPort(Protocol):
    """產碼用的 LLM 介面。對話層另外使用 LangChain 的 tool-calling agent。"""

    def complete(
        self,
        system: str,
        user: str,
        *,
        name: str = "",
        metadata: dict[str, Any] | None = None,
        effort: str = "normal",
    ) -> str:
        """effort="high" 代表困難修正，adapter 可切換到較強的推理設定（例如啟用 thinking）。"""
        ...

    def stream(self, system: str, user: str, *, name: str = "") -> Iterator[str]: ...


class PromptStorePort(Protocol):
    def get_active(self) -> SystemPromptVersion: ...

    def save_new_version(self, content: str, note: str) -> SystemPromptVersion: ...

    def reset_to_default(self) -> SystemPromptVersion: ...

    def history(self) -> Sequence[SystemPromptVersion]: ...


class SettingsRepository(Protocol):
    def load_llm_settings(self) -> LlmSettings: ...

    def save_llm_settings(self, settings: LlmSettings) -> None: ...

    def load_repo_settings(self) -> RepoSettings: ...

    def save_repo_settings(self, settings: RepoSettings) -> None: ...


class ConversationRepository(Protocol):
    def get(self, conversation_id: str) -> Conversation | None: ...

    def save(self, conversation: Conversation) -> None: ...


class RunRepository(Protocol):
    def get(self, run_id: str) -> FeatureRun | None: ...

    def save(self, run: FeatureRun) -> None: ...

    def list_recent(self, limit: int = 20) -> Sequence[FeatureRun]: ...


class CodeRepositoryPort(Protocol):
    """Git + GitHub 操作。"""

    def prepare_workspace(self, repo: RepoSettings, branch: str, workspace: Path) -> Path:
        """clone 目標 repo 到 workspace（若為空 repo 則初始化 base branch），建立並切換到 branch。"""
        ...

    def write_files(self, workspace: Path, files: Sequence[GeneratedFile]) -> None: ...

    def delete_files(self, workspace: Path, deletions: Sequence[FileDeletion]) -> None: ...

    def read_files(self, workspace: Path, patterns: Sequence[str]) -> list[GeneratedFile]:
        """依 glob 樣式讀取 workspace 中的文字檔（排除 .git 與 build 目錄）。"""
        ...

    def commit(self, workspace: Path, message: str) -> str:
        """回傳 commit sha；若無變更則回傳空字串。"""
        ...

    def push_branch(self, workspace: Path, branch: str) -> None: ...

    def open_pull_request(
        self, repo: RepoSettings, branch: str, title: str, body: str, *, draft: bool = False
    ) -> PullRequest: ...

    def update_pull_request(
        self,
        repo: RepoSettings,
        pull_request: PullRequest,
        *,
        title: str | None = None,
        body: str | None = None,
        ready_for_review: bool = False,
    ) -> PullRequest: ...


class BuildRunnerPort(Protocol):
    def run_tests(self, workspace: Path, *, test_filter: str | None = None) -> BuildResult: ...


class ScaffolderPort(Protocol):
    def scaffold(self, project_name: str, base_package: str, feature_slug: str) -> list[GeneratedFile]: ...


class IdGenerator(Protocol):
    def __call__(self) -> str: ...


EventSink = Callable[[FeatureRun], None]
