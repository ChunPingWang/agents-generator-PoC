"""CodeRepositoryPort 的 GitPython + PyGithub 實作。

- token 來源：建構子參數（來自環境變數），或退回 `gh auth token`（開發機）。
- 空 repo（無 base branch）時會先建立初始 commit 並推送 base branch，PR 才有 base 可比對。
"""

from __future__ import annotations

import fnmatch
import logging
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from git import GitCommandError, Repo

from java_coding_agent.domain.models import FileDeletion, GeneratedFile, PullRequest, RepoSettings

log = logging.getLogger(__name__)
_EXCLUDED_DIRS = {".git", "build", ".gradle", "out", "node_modules"}


class GitHubClientFactory:
    """隔離 PyGithub，方便測試以 fake 取代。"""

    def __init__(self, token: str) -> None:
        self._token = token

    def _repo(self, full_name: str):
        from github import Auth, Github

        return Github(auth=Auth.Token(self._token)).get_repo(full_name)

    def create_pull(
        self, full_name: str, *, title: str, body: str, head: str, base: str, draft: bool = False
    ) -> PullRequest:
        from github import GithubException

        repo = self._repo(full_name)
        try:
            pr = repo.create_pull(title=title, body=body, head=head, base=base, draft=draft)
        except GithubException as exc:
            if exc.status == 422:  # 多半是 PR 已存在
                existing = list(repo.get_pulls(state="open", head=f"{repo.owner.login}:{head}", base=base))
                if existing:
                    pr = existing[0]
                    pr.edit(title=title, body=body)
                else:
                    raise
            else:
                raise
        return PullRequest(url=pr.html_url, number=pr.number, branch=head, draft=bool(pr.draft))

    def update_pull(
        self,
        full_name: str,
        number: int,
        *,
        title: str | None = None,
        body: str | None = None,
        ready_for_review: bool = False,
    ) -> PullRequest:
        pr = self._repo(full_name).get_pull(number)
        kwargs = {k: v for k, v in {"title": title, "body": body}.items() if v is not None}
        if kwargs:
            pr.edit(**kwargs)
        if ready_for_review and pr.draft:
            pr.mark_ready_for_review()
            pr = self._repo(full_name).get_pull(number)
        return PullRequest(url=pr.html_url, number=pr.number, branch=pr.head.ref, draft=bool(pr.draft))


class GitGitHubRepository:
    def __init__(
        self,
        *,
        token: str | None,
        author_name: str = "Java Coding Agent",
        author_email: str = "java-coding-agent@users.noreply.github.com",
        pr_client: Any | None = None,
    ) -> None:
        self._token = token or _gh_cli_token()
        if not self._token:
            raise RuntimeError("找不到 GitHub token：請設定 GITHUB_TOKEN，或先執行 `gh auth login`")
        self._author = (author_name, author_email)
        self._pr_client = pr_client or GitHubClientFactory(self._token)

    # ------------------------------------------------------------------ port
    def prepare_workspace(self, repo: RepoSettings, branch: str, workspace: Path) -> Path:
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.parent.mkdir(parents=True, exist_ok=True)
        git_repo = Repo.clone_from(self._authed_url(repo), workspace)
        self._configure(git_repo)

        remote_heads = {ref.remote_head for ref in git_repo.remotes.origin.refs if ref.remote_head != "HEAD"}
        if repo.base_branch not in remote_heads:
            log.info("遠端沒有 %s 分支（空 repo），建立初始 commit", repo.base_branch)
            git_repo.git.checkout("--orphan", repo.base_branch) if git_repo.heads else git_repo.git.checkout(
                "-b", repo.base_branch
            )
            (workspace / ".gitkeep").write_text("", encoding="utf-8")
            git_repo.git.add(A=True)
            git_repo.index.commit("chore: 初始化 repo", author=self._actor(), committer=self._actor())
            git_repo.git.push("-u", "origin", repo.base_branch)
        else:
            git_repo.git.checkout(repo.base_branch)
            git_repo.git.pull("origin", repo.base_branch, "--ff-only")

        git_repo.git.checkout("-B", branch)
        return workspace

    def write_files(self, workspace: Path, files: Sequence[GeneratedFile]) -> None:
        for f in files:
            target = workspace / f.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f.content, encoding="utf-8")

    def delete_files(self, workspace: Path, deletions: Sequence[FileDeletion]) -> None:
        for d in deletions:
            target = workspace / d.path
            if target.is_file():
                target.unlink()

    def read_files(self, workspace: Path, patterns: Sequence[str]) -> list[GeneratedFile]:
        out: list[GeneratedFile] = []
        for path in sorted(workspace.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(workspace).as_posix()
            if any(part in _EXCLUDED_DIRS for part in rel.split("/")[:-1]):
                continue
            if any(fnmatch.fnmatch(rel, p) for p in patterns):
                try:
                    out.append(GeneratedFile(rel, path.read_text(encoding="utf-8")))
                except UnicodeDecodeError:
                    continue
        return out

    def commit(self, workspace: Path, message: str) -> str:
        git_repo = Repo(workspace)
        git_repo.git.add(A=True)
        # 六角形架構的 `port/out` 會被常見的 `out/` ignore 規則吃掉；src 底下的原始碼一律強制納入版本控制
        git_repo.git.add("-f", "--", ":(glob)**/src/**")
        if not git_repo.index.diff("HEAD") and not git_repo.untracked_files:
            return ""
        commit = git_repo.index.commit(message, author=self._actor(), committer=self._actor())
        return commit.hexsha

    def push_branch(self, workspace: Path, branch: str) -> None:
        git_repo = Repo(workspace)
        try:
            git_repo.git.push("-u", "origin", branch, "--force-with-lease")
        except GitCommandError:
            git_repo.git.push("-u", "origin", branch, "--force")

    def open_pull_request(
        self, repo: RepoSettings, branch: str, title: str, body: str, *, draft: bool = False
    ) -> PullRequest:
        return self._pr_client.create_pull(
            repo.full_name, title=title, body=body, head=branch, base=repo.base_branch, draft=draft
        )

    def update_pull_request(
        self,
        repo: RepoSettings,
        pull_request: PullRequest,
        *,
        title: str | None = None,
        body: str | None = None,
        ready_for_review: bool = False,
    ) -> PullRequest:
        return self._pr_client.update_pull(
            repo.full_name, pull_request.number, title=title, body=body, ready_for_review=ready_for_review
        )

    # ------------------------------------------------------------------ helpers
    def _authed_url(self, repo: RepoSettings) -> str:
        return f"https://x-access-token:{self._token}@github.com/{repo.full_name}.git"

    def _configure(self, git_repo: Repo) -> None:
        with git_repo.config_writer() as cfg:
            cfg.set_value("user", "name", self._author[0])
            cfg.set_value("user", "email", self._author[1])
            cfg.set_value("commit", "gpgsign", "false")

    def _actor(self):
        from git import Actor

        return Actor(*self._author)


def _gh_cli_token() -> str | None:
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        out = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except Exception:
        return None
