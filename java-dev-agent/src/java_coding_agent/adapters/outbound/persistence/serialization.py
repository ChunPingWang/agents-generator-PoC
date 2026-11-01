"""domain 物件 <-> JSON dict。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from java_coding_agent.domain.models import (
    Conversation,
    FeatureRun,
    LlmProvider,
    LlmSettings,
    Message,
    PullRequest,
    RepoSettings,
    Role,
    RunEvent,
    RunStage,
    TaskItem,
)


def llm_settings_to_dict(s: LlmSettings) -> dict[str, Any]:
    return {
        "provider": s.provider.value,
        "base_url": s.base_url,
        "model": s.model,
        "temperature": s.temperature,
        "context_length": s.context_length,
        "seed": s.seed,
        "api_key_env": s.api_key_env,
    }


def llm_settings_from_dict(d: dict[str, Any]) -> LlmSettings:
    return LlmSettings(
        provider=LlmProvider(d["provider"]),
        base_url=d["base_url"],
        model=d["model"],
        temperature=d["temperature"],
        context_length=d["context_length"],
        seed=d.get("seed"),
        api_key_env=d.get("api_key_env"),
    )


def repo_settings_to_dict(r: RepoSettings) -> dict[str, Any]:
    return {"url": r.url, "base_branch": r.base_branch}


def repo_settings_from_dict(d: dict[str, Any]) -> RepoSettings:
    return RepoSettings(url=d["url"], base_branch=d["base_branch"])


def conversation_to_rows(c: Conversation) -> list[dict[str, Any]]:
    return [{"role": m.role.value, "content": m.content, "created_at": m.created_at.isoformat()} for m in c.messages]


def conversation_from_rows(conversation_id: str, rows: list[dict[str, Any]]) -> Conversation:
    return Conversation(
        id=conversation_id,
        messages=[
            Message(role=Role(r["role"]), content=r["content"], created_at=datetime.fromisoformat(r["created_at"]))
            for r in rows
        ],
    )


def run_to_dict(run: FeatureRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "requirement": run.requirement,
        "stage": run.stage.value,
        "feature_slug": run.feature_slug,
        "spec_md": run.spec_md,
        "plan_md": run.plan_md,
        "tasks": [
            {"id": t.id, "title": t.title, "description": t.description, "test_hint": t.test_hint, "done": t.done}
            for t in run.tasks
        ],
        "files": run.files,
        "events": [
            {
                "stage": e.stage.value,
                "message": e.message,
                "level": e.level,
                "created_at": e.created_at.isoformat(),
                "data": e.data,
            }
            for e in run.events
        ],
        "pull_request": (
            {
                "url": run.pull_request.url,
                "number": run.pull_request.number,
                "branch": run.pull_request.branch,
                "draft": run.pull_request.draft,
            }
            if run.pull_request
            else None
        ),
        "branch": run.branch,
        "error": run.error,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


def run_from_dict(d: dict[str, Any]) -> FeatureRun:
    run = FeatureRun(
        id=d["id"],
        requirement=d["requirement"],
        stage=RunStage(d["stage"]),
        feature_slug=d.get("feature_slug", ""),
        spec_md=d.get("spec_md", ""),
        plan_md=d.get("plan_md", ""),
        tasks=[TaskItem(**t) for t in d.get("tasks", [])],
        files=d.get("files", {}),
        events=[
            RunEvent(
                stage=RunStage(e["stage"]),
                message=e["message"],
                level=e.get("level", "info"),
                created_at=datetime.fromisoformat(e["created_at"]),
                data=e.get("data", {}),
            )
            for e in d.get("events", [])
        ],
        pull_request=PullRequest(**d["pull_request"]) if d.get("pull_request") else None,
        branch=d.get("branch", ""),
        error=d.get("error"),
        created_at=datetime.fromisoformat(d["created_at"]),
        updated_at=datetime.fromisoformat(d["updated_at"]),
    )
    return run
