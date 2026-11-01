"""測試用 fakes：實作 domain / application ports，不觸及外部系統。"""

from __future__ import annotations

import fnmatch
import itertools
from collections import defaultdict, deque
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from java_coding_agent.domain.models import (
    BuildResult,
    Conversation,
    FeatureRun,
    GeneratedFile,
    LlmSettings,
    PullRequest,
    RepoSettings,
    SystemPromptVersion,
)


class FakeLlm:
    """依 `name` 回放腳本化回應；record 保留所有呼叫以便斷言。"""

    def __init__(self, scripts: dict[str, list[str]] | None = None, default: str = "") -> None:
        self._scripts: dict[str, deque[str]] = {k: deque(v) for k, v in (scripts or {}).items()}
        self._default = default
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        system: str,
        user: str,
        *,
        name: str = "",
        metadata: dict[str, Any] | None = None,
        effort: str = "normal",
    ) -> str:
        self.calls.append({"name": name, "system": system, "user": user, "metadata": metadata, "effort": effort})
        q = self._scripts.get(name)
        if q:
            return q.popleft() if len(q) > 1 else q[0]
        return self._default

    def stream(self, system: str, user: str, *, name: str = "") -> Iterator[str]:
        yield self.complete(system, user, name=name)

    def calls_named(self, name: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["name"] == name]


class FakeCodeRepository:
    def __init__(self, existing: dict[str, str] | None = None, fail_push: bool = False) -> None:
        self.files: dict[str, str] = dict(existing or {})
        self.commits: list[str] = []
        self.pushed: list[str] = []
        self.prs: list[dict[str, Any]] = []
        self.prepared: list[tuple[RepoSettings, str]] = []
        self._fail_push = fail_push

    def prepare_workspace(self, repo: RepoSettings, branch: str, workspace: Path) -> Path:
        self.prepared.append((repo, branch))
        return workspace

    def write_files(self, workspace: Path, files: Sequence[GeneratedFile]) -> None:
        for f in files:
            self.files[f.path] = f.content

    def delete_files(self, workspace: Path, deletions) -> None:
        for d in deletions:
            self.files.pop(d.path, None)

    def read_files(self, workspace: Path, patterns: Sequence[str]) -> list[GeneratedFile]:
        out = []
        for path in sorted(self.files):
            if any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch("/" + path, p) for p in patterns):
                out.append(GeneratedFile(path, self.files[path]))
        return out

    def commit(self, workspace: Path, message: str) -> str:
        self.commits.append(message)
        return f"sha{len(self.commits)}"

    def push_branch(self, workspace: Path, branch: str) -> None:
        if self._fail_push:
            raise RuntimeError("push rejected")
        self.pushed.append(branch)

    def open_pull_request(
        self, repo: RepoSettings, branch: str, title: str, body: str, *, draft: bool = False
    ) -> PullRequest:
        self.prs.append({"repo": repo, "branch": branch, "title": title, "body": body, "draft": draft, "updates": []})
        return PullRequest(
            url=f"https://github.com/{repo.full_name}/pull/{len(self.prs)}",
            number=len(self.prs),
            branch=branch,
            draft=draft,
        )

    def update_pull_request(self, repo, pull_request, *, title=None, body=None, ready_for_review=False):
        pr = self.prs[pull_request.number - 1]
        if title is not None:
            pr["title"] = title
        if body is not None:
            pr["body"] = body
        if ready_for_review:
            pr["draft"] = False
        pr["updates"].append({"title": title, "body": body, "ready": ready_for_review})
        return PullRequest(
            url=pull_request.url, number=pull_request.number, branch=pull_request.branch, draft=pr["draft"]
        )


class FakeBuildRunner:
    """依序回放結果；用完後重複最後一個。"""

    def __init__(self, results: Sequence[bool]) -> None:
        self._results = deque(results)
        self.calls = 0

    def run_tests(self, workspace: Path, *, test_filter: str | None = None) -> BuildResult:
        self.calls += 1
        ok = self._results.popleft() if len(self._results) > 1 else self._results[0]
        return BuildResult(
            success=ok, exit_code=0 if ok else 1, stdout="" if ok else "FooTest > x FAILED\nexpected 1 but was 2"
        )


class FakeScaffolder:
    def scaffold(self, project_name: str, base_package: str, feature_slug: str) -> list[GeneratedFile]:
        return [
            GeneratedFile("settings.gradle.kts", f'rootProject.name = "{project_name}"\n'),
            GeneratedFile("build.gradle.kts", "// root\n"),
            GeneratedFile(
                f"bootstrap/src/test/java/{base_package.replace('.', '/')}/ArchitectureTest.java", "class A {}\n"
            ),
        ]


class InMemoryPromptStore:
    def __init__(self, default: str) -> None:
        self._default = default
        self._versions: list[SystemPromptVersion] = [SystemPromptVersion(1, default, "預設", is_default=True)]

    def get_active(self) -> SystemPromptVersion:
        return self._versions[-1]

    def save_new_version(self, content: str, note: str) -> SystemPromptVersion:
        v = SystemPromptVersion(len(self._versions) + 1, content, note)
        self._versions.append(v)
        return v

    def reset_to_default(self) -> SystemPromptVersion:
        v = SystemPromptVersion(len(self._versions) + 1, self._default, "還原預設", is_default=True)
        self._versions.append(v)
        return v

    def history(self) -> Sequence[SystemPromptVersion]:
        return list(self._versions)


class InMemorySettings:
    def __init__(self, llm: LlmSettings | None = None, repo: RepoSettings | None = None) -> None:
        self.llm = llm or LlmSettings()
        self.repo = repo or RepoSettings()

    def load_llm_settings(self) -> LlmSettings:
        return self.llm

    def save_llm_settings(self, settings: LlmSettings) -> None:
        self.llm = settings

    def load_repo_settings(self) -> RepoSettings:
        return self.repo

    def save_repo_settings(self, settings: RepoSettings) -> None:
        self.repo = settings


class InMemoryConversations:
    def __init__(self) -> None:
        self.items: dict[str, Conversation] = {}

    def get(self, conversation_id: str) -> Conversation | None:
        return self.items.get(conversation_id)

    def save(self, conversation: Conversation) -> None:
        self.items[conversation.id] = conversation


class InMemoryRuns:
    def __init__(self) -> None:
        self.items: dict[str, FeatureRun] = {}

    def get(self, run_id: str) -> FeatureRun | None:
        return self.items.get(run_id)

    def save(self, run: FeatureRun) -> None:
        self.items[run.id] = run

    def list_recent(self, limit: int = 20) -> Sequence[FeatureRun]:
        return sorted(self.items.values(), key=lambda r: r.created_at, reverse=True)[:limit]


def sequential_ids(prefix: str = "id") -> Any:
    counter = itertools.count(1)
    return lambda: f"{prefix}-{next(counter)}"


# ----------------------------------------------------------------- 腳本化 LLM 輸出
SPEC_OUTPUT = """=== FILE: specs/001-{slug}/spec.md ===
# Spec 001 — 個人資料服務
## 驗收條件
```gherkin
功能: 個人資料
  場景: 新增
```
=== END FILE ===
FEATURE_NAME: personal-profile-crud
"""

PLAN_OUTPUT = """=== FILE: specs/001-personal-profile-crud/plan.md ===
# Plan
## 類別清單
| domain/src/main/java/com/github/x/y/domain/Profile.java | 個人資料 |
=== END FILE ===
"""

TASKS_OUTPUT = """=== FILE: specs/001-personal-profile-crud/tasks.md ===
# Tasks 001 — 個人資料服務

## T1 建立 Profile 領域模型
- 說明：建立 domain/Profile.java record 與不變條件
- 測試：domain/src/test/java/com/github/x/y/domain/ProfileTest.java 驗證空名稱拒絕

## T2 建立 CreateProfile use case
- 說明：application/port/in/CreateProfileUseCase.java 與實作
- 測試：application/src/test/java/.../CreateProfileServiceTest.java
=== END FILE ===
"""

TEST_OUTPUT = """=== FILE: domain/src/test/java/{P}/domain/ProfileTest.java ===
package {B}.domain;
class ProfileTest {}
=== END FILE ===
"""

IMPL_OUTPUT = """=== FILE: domain/src/main/java/{P}/domain/Profile.java ===
package {B}.domain;
public record Profile(String name) {}
=== END FILE ===
"""

FIX_OUTPUT = """=== FILE: domain/src/main/java/{P}/domain/Profile.java ===
package {B}.domain;
public record Profile(String name) { }
=== END FILE ===
"""

PR_BODY_OUTPUT = """## 摘要
實作個人資料服務。
## Tasks
- [x] T1 建立 Profile 領域模型
- [x] T2 建立 CreateProfile use case
"""


DEFAULT_BASE_PACKAGE = "com.github.chunpingwang.ollamaagentoutcome"


def _fill(template: str, base_package: str) -> str:
    return template.replace("{B}", base_package).replace("{P}", base_package.replace(".", "/"))


def scripted_llm(base_package: str = DEFAULT_BASE_PACKAGE, **overrides: list[str]) -> FakeLlm:
    scripts: dict[str, list[str]] = {
        "sdd.specify": [SPEC_OUTPUT],
        "sdd.plan": [PLAN_OUTPUT],
        "sdd.tasks": [TASKS_OUTPUT],
        "sdd.write_test": [_fill(TEST_OUTPUT, base_package)],
        "sdd.write_impl": [_fill(IMPL_OUTPUT, base_package)],
        "sdd.fix": [_fill(FIX_OUTPUT, base_package)],
        "sdd.pr_body": [PR_BODY_OUTPUT],
    }
    scripts.update(overrides)
    return FakeLlm(scripts)


_ = defaultdict  # 保留以供未來擴充
