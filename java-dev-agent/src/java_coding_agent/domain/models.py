"""領域模型。全部為不可變值物件或簡單可變實體，不含框架相依。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from java_coding_agent.domain.errors import ValidationError


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# LLM 設定
# ---------------------------------------------------------------------------
class LlmProvider(StrEnum):
    OLLAMA = "ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


@dataclass(frozen=True)
class LlmSettings:
    provider: LlmProvider = LlmProvider.OLLAMA
    base_url: str = "http://localhost:11434"
    model: str = "qwen3.6:35b-a3b-coding-mxfp8"
    temperature: float = 0.0
    context_length: int = 32768
    seed: int | None = 42
    api_key_env: str | None = None
    """存放 API key 的環境變數名稱；key 本身永不進入模型或資料庫。"""

    def __post_init__(self) -> None:
        if not 0.0 <= self.temperature <= 2.0:
            raise ValidationError(f"temperature 必須在 0 到 2 之間，收到 {self.temperature}")
        if self.context_length < 1024:
            raise ValidationError(f"context_length 至少 1024，收到 {self.context_length}")
        if not self.model.strip():
            raise ValidationError("model 不可為空")
        if not self.base_url.strip():
            raise ValidationError("base_url 不可為空")

    def updated(self, **changes: Any) -> LlmSettings:
        if "provider" in changes and isinstance(changes["provider"], str):
            try:
                changes["provider"] = LlmProvider(changes["provider"].lower())
            except ValueError as exc:
                allowed = ", ".join(p.value for p in LlmProvider)
                raise ValidationError(f"provider 必須為 {allowed}") from exc
        return replace(self, **changes)


# ---------------------------------------------------------------------------
# 目標 GitHub repo 設定
# ---------------------------------------------------------------------------
_GITHUB_URL = re.compile(
    r"^(?:https://github\.com/|git@github\.com:)(?P<owner>[\w.-]+)/(?P<name>[\w.-]+?)(?:\.git)?/?$"
)


@dataclass(frozen=True)
class RepoSettings:
    url: str = "https://github.com/ChunPingWang/ollama-agent-outcome.git"
    base_branch: str = "main"

    def __post_init__(self) -> None:
        if not _GITHUB_URL.match(self.url):
            raise ValidationError(f"不是合法的 GitHub repo URL：{self.url}")
        if not re.match(r"^[\w./-]+$", self.base_branch):
            raise ValidationError(f"不是合法的分支名稱：{self.base_branch}")

    @property
    def owner(self) -> str:
        return _GITHUB_URL.match(self.url).group("owner")  # type: ignore[union-attr]

    @property
    def name(self) -> str:
        return _GITHUB_URL.match(self.url).group("name")  # type: ignore[union-attr]

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    def updated(self, **changes: Any) -> RepoSettings:
        return replace(self, **changes)


# ---------------------------------------------------------------------------
# System Prompt 版本
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SystemPromptVersion:
    version: int
    content: str
    note: str = ""
    created_at: datetime = field(default_factory=utcnow)
    is_default: bool = False


@dataclass(frozen=True)
class PendingPromptChange:
    """使用者尚未確認的 System Prompt 變更提案。"""

    change_id: str
    new_content: str
    summary: str
    created_at: datetime = field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# 對話
# ---------------------------------------------------------------------------
class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


@dataclass(frozen=True)
class Message:
    role: Role
    content: str
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class Conversation:
    id: str
    messages: list[Message] = field(default_factory=list)

    def add(self, role: Role, content: str) -> Message:
        message = Message(role=role, content=content)
        self.messages.append(message)
        return message


# ---------------------------------------------------------------------------
# SDD 流程
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GeneratedFile:
    path: str
    content: str

    def __post_init__(self) -> None:
        normalized = self.path.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValidationError(f"檔案路徑必須是相對路徑且不可含 ..：{self.path}")
        object.__setattr__(self, "path", normalized)


@dataclass(frozen=True)
class FileDeletion:
    path: str

    def __post_init__(self) -> None:
        normalized = self.path.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValidationError(f"檔案路徑必須是相對路徑且不可含 ..：{self.path}")
        object.__setattr__(self, "path", normalized)


@dataclass(frozen=True)
class TaskItem:
    id: str
    title: str
    description: str = ""
    test_hint: str = ""
    done: bool = False

    def completed(self) -> TaskItem:
        return replace(self, done=True)


@dataclass(frozen=True)
class BuildResult:
    success: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0

    def summary(self, max_chars: int = 4000) -> str:
        """給 LLM 修正用的精簡輸出。

        保留：編譯錯誤（含其後兩行定位資訊）、Gradle 測試失敗標題與其下的例外訊息、`Caused by`、
        統計行（`N tests completed, M failed`）、`What went wrong` 區塊。
        排除：stack frame（`at ...`、`... N more`），否則數百行 frame 會把真正的錯誤訊息擠掉。
        """
        text = (self.stderr + "\n" + self.stdout).strip()
        lines = text.splitlines()
        keep: list[str] = []
        carry = 0
        for ln in lines:
            stripped = ln.strip()
            if not stripped or stripped.startswith("at ") or re.match(r"^\.\.\. \d+ more$", stripped):
                continue
            if carry > 0:
                keep.append(ln.rstrip())
                carry -= 1
                continue
            if re.search(r"\berror:", ln):  # javac：保留原始碼行、caret、以及 `symbol:` / `location:` 說明
                keep.append(ln.rstrip())
                carry = 4
            elif re.search(r"> \S.* FAILED$|FAILED$", stripped):  # Gradle 測試失敗標題：保留其後兩行（例外與訊息）
                keep.append(ln.rstrip())
                carry = 2
            elif re.search(r"Caused by:?\s*$", stripped):  # Gradle 主控台：例外訊息在下一行
                keep.append(ln.rstrip())
                carry = 1
            elif re.search(
                r"Caused by:|tests completed|What went wrong|Could not|expected|but was|Exception:|Error:", ln
            ):
                keep.append(ln.rstrip())
            elif re.search(r"^\s*@\S+\(\"|^\s*The step '|is undefined|are undefined|public void \S+\(", ln):
                # Cucumber undefined step：保留步驟文字與官方建議的 snippet 註解，模型才能寫出逐字相同的步驟定義
                keep.append(ln.rstrip())
        # 去除連續重複行，並截斷過長的單行（例如 MergedContextConfiguration 描述）以免擠掉 Caused by 鏈
        deduped: list[str] = []
        for ln in keep:
            ln = ln if len(ln) <= 400 else ln[:400] + " …"
            if not deduped or deduped[-1] != ln:
                deduped.append(ln)
        joined = "\n".join(deduped) if deduped else "\n".join(lines[-40:])
        return joined[:max_chars] if len(joined) > max_chars else joined


class RunStage(StrEnum):
    PENDING = "pending"
    SPECIFY = "specify"
    PLAN = "plan"
    TASKS = "tasks"
    SCAFFOLD = "scaffold"
    IMPLEMENT = "implement"
    VERIFY = "verify"
    PUBLISH = "publish"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (RunStage.SUCCEEDED, RunStage.FAILED)


@dataclass(frozen=True)
class RunEvent:
    stage: RunStage
    message: str
    level: str = "info"
    created_at: datetime = field(default_factory=utcnow)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PullRequest:
    url: str
    number: int
    branch: str
    draft: bool = False


@dataclass
class FeatureRun:
    id: str
    requirement: str
    stage: RunStage = RunStage.PENDING
    feature_slug: str = ""
    spec_md: str = ""
    plan_md: str = ""
    tasks: list[TaskItem] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    events: list[RunEvent] = field(default_factory=list)
    pull_request: PullRequest | None = None
    branch: str = ""
    error: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    def record(self, stage: RunStage, message: str, level: str = "info", **data: Any) -> RunEvent:
        event = RunEvent(stage=stage, message=message, level=level, data=data)
        self.events.append(event)
        self.stage = stage
        self.updated_at = utcnow()
        return event

    def fail(self, stage: RunStage, error: str) -> None:
        self.error = f"[{stage.value}] {error}"
        self.record(RunStage.FAILED, self.error, level="error", failed_stage=stage.value)

    def succeed(self, pull_request: PullRequest) -> None:
        self.pull_request = pull_request
        self.record(RunStage.SUCCEEDED, f"PR 已建立：{pull_request.url}", pr_url=pull_request.url)


def slugify(text: str, max_len: int = 40) -> str:
    """產生分支 / 目錄可用的 slug；非 ASCII 字元會被移除，若結果為空則回傳 'feature'。"""
    ascii_only = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return (ascii_only[:max_len].strip("-")) or "feature"
