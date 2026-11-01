"""FastAPI inbound adapter：聊天（SSE 串流）、Run 進度（SSE）、設定與 System Prompt API、靜態聊天頁。"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from java_coding_agent.domain.errors import DomainError
from java_coding_agent.domain.models import FeatureRun, RunEvent

_STATIC = Path(__file__).parent / "static"


class ChatRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=20000)


class LlmSettingsUpdate(BaseModel):
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    temperature: float | None = None
    context_length: int | None = None


class RepoSettingsUpdate(BaseModel):
    url: str | None = None
    base_branch: str | None = None


class PromptProposal(BaseModel):
    new_content: str
    summary: str = ""


class RunRequest(BaseModel):
    requirement: str = Field(min_length=1)


def _run_json(run: FeatureRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "requirement": run.requirement,
        "stage": run.stage.value,
        "feature_slug": run.feature_slug,
        "tasks": [{"id": t.id, "title": t.title, "done": t.done} for t in run.tasks],
        "pull_request": run.pull_request.url if run.pull_request else None,
        "pull_request_draft": run.pull_request.draft if run.pull_request else None,
        "branch": run.branch,
        "error": run.error,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "events": [_event_json(e) for e in run.events],
        "spec_md": run.spec_md,
        "plan_md": run.plan_md,
    }


def _event_json(e: RunEvent) -> dict[str, Any]:
    return {
        "stage": e.stage.value,
        "message": e.message,
        "level": e.level,
        "at": e.created_at.isoformat(),
        "data": e.data,
    }


async def _iterate_in_thread(make_iter) -> AsyncIterator[Any]:
    """把同步 generator 丟到背景執行緒，逐項以 async 方式 yield（避免阻塞事件迴圈）。"""
    q: queue.Queue = queue.Queue()
    sentinel = object()

    def worker() -> None:
        try:
            for item in make_iter():
                q.put(item)
        except Exception as exc:  # 把例外帶回事件迴圈
            q.put(exc)
        finally:
            q.put(sentinel)

    threading.Thread(target=worker, daemon=True).start()
    while True:
        item = await anyio.to_thread.run_sync(q.get)
        if item is sentinel:
            return
        if isinstance(item, Exception):
            raise item
        yield item


def create_app(container) -> FastAPI:
    app = FastAPI(title="Java Coding Agent", version="0.1.0")
    app.state.container = container

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "langfuse": container.tracing.enabled}

    # ------------------------------------------------------------- chat
    @app.post("/api/chat")
    async def chat(req: ChatRequest):
        def generator() -> Iterator[dict[str, Any]]:
            for chunk in container.chat.stream(req.conversation_id, req.message):
                yield {
                    "event": chunk.kind,
                    "data": json.dumps({"content": chunk.content, "data": chunk.data}, ensure_ascii=False),
                }

        async def events():
            try:
                async for item in _iterate_in_thread(generator):
                    yield item
            except DomainError as exc:
                yield {"event": "error", "data": json.dumps({"content": str(exc)}, ensure_ascii=False)}
            except Exception as exc:
                yield {
                    "event": "error",
                    "data": json.dumps({"content": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False),
                }
            yield {"event": "done", "data": "{}"}

        return EventSourceResponse(events())

    # ------------------------------------------------------------- runs
    @app.post("/api/runs", status_code=202)
    def start_run(req: RunRequest) -> dict[str, Any]:
        return _run_json(container.runs.start(req.requirement))

    @app.get("/api/runs")
    def list_runs() -> list[dict[str, Any]]:
        return [_run_json(r) for r in container.runs.list_recent()]

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        run = container.runs.get(run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        return _run_json(run)

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str):
        if container.runs.get(run_id) is None:
            raise HTTPException(404, "run not found")

        def generator():
            for e in container.runs.subscribe(run_id):
                yield {"event": "run_event", "data": json.dumps(_event_json(e), ensure_ascii=False)}

        async def events():
            async for item in _iterate_in_thread(generator):
                yield item
            run = container.runs.get(run_id)
            yield {"event": "done", "data": json.dumps(_run_json(run), ensure_ascii=False)}

        return EventSourceResponse(events())

    # ------------------------------------------------------------- settings
    @app.get("/api/settings/llm")
    def get_llm() -> dict[str, Any]:
        s = container.llm_settings.current()
        return {
            "provider": s.provider.value,
            "base_url": s.base_url,
            "model": s.model,
            "temperature": s.temperature,
            "context_length": s.context_length,
        }

    @app.put("/api/settings/llm")
    def put_llm(body: LlmSettingsUpdate) -> dict[str, Any]:
        try:
            container.llm_settings.update(**body.model_dump())
        except DomainError as exc:
            raise HTTPException(400, str(exc)) from exc
        return get_llm()

    @app.get("/api/settings/repo")
    def get_repo() -> dict[str, Any]:
        r = container.repo_settings.current()
        return {"url": r.url, "base_branch": r.base_branch, "full_name": r.full_name}

    @app.put("/api/settings/repo")
    def put_repo(body: RepoSettingsUpdate) -> dict[str, Any]:
        try:
            container.repo_settings.update(url=body.url, base_branch=body.base_branch)
        except DomainError as exc:
            raise HTTPException(400, str(exc)) from exc
        return get_repo()

    # ------------------------------------------------------------- system prompt
    @app.get("/api/system-prompt")
    def get_prompt() -> dict[str, Any]:
        v = container.system_prompt.current()
        return {"version": v.version, "content": v.content, "note": v.note, "is_default": v.is_default}

    @app.get("/api/system-prompt/history")
    def prompt_history() -> list[dict[str, Any]]:
        return [
            {"version": v.version, "note": v.note, "is_default": v.is_default, "created_at": v.created_at.isoformat()}
            for v in container.system_prompt.history()
        ]

    @app.post("/api/system-prompt/propose")
    def propose_prompt(body: PromptProposal) -> dict[str, Any]:
        try:
            c = container.system_prompt.propose(body.new_content, body.summary)
        except DomainError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"change_id": c.change_id, "summary": c.summary}

    @app.post("/api/system-prompt/confirm/{change_id}")
    def confirm_prompt(change_id: str) -> dict[str, Any]:
        try:
            container.system_prompt.confirm(change_id)
        except DomainError as exc:
            raise HTTPException(400, str(exc)) from exc
        return get_prompt()

    @app.post("/api/system-prompt/reset")
    def reset_prompt() -> dict[str, Any]:
        container.system_prompt.reset()
        return get_prompt()

    # ------------------------------------------------------------- static UI
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(_STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=_STATIC), name="static")
    return app
