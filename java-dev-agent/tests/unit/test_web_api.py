"""FastAPI adapter 測試：以 fakes 組裝 Container，不需要真實 LLM。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from java_coding_agent.adapters.inbound.web.app import create_app
from java_coding_agent.application.chat_service import ChatService
from java_coding_agent.application.run_registry import RunRegistry
from java_coding_agent.application.settings_service import LlmSettingsService, RepoSettingsService
from java_coding_agent.application.system_prompt_service import SystemPromptService
from tests.unit.fakes import InMemoryConversations, InMemoryRuns, InMemorySettings, sequential_ids
from tests.unit.test_chat_service import FakeToolChatModel, StubFactory, tool_call


@pytest.fixture
def client(make_pipeline, prompt_store, templates):
    settings = InMemorySettings()

    def factory(on_event):
        p = make_pipeline()
        p._on_event = on_event
        return p

    runs = RunRegistry(pipeline_factory=factory, run_repository=InMemoryRuns(), id_generator=sequential_ids("run"))
    model = FakeToolChatModel(
        responses=[tool_call("start_feature_run", business_requirement="CRUD"), AIMessage(content="已啟動")]
    )
    model.seen = []
    chat = ChatService(
        model_factory=StubFactory(model),
        llm_settings=LlmSettingsService(settings),
        repo_settings=RepoSettingsService(settings),
        system_prompt=SystemPromptService(prompt_store, sequential_ids("chg")),
        runs=runs,
        conversations=InMemoryConversations(),
        templates=templates,
    )
    container = SimpleNamespace(
        chat=chat,
        runs=runs,
        system_prompt=chat._system_prompt,
        llm_settings=chat._llm_settings,
        repo_settings=chat._repo_settings,
        tracing=SimpleNamespace(enabled=False),
    )
    with TestClient(create_app(container)) as c:
        yield c
    runs.shutdown()


def _sse_events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        event, data = "message", ""
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if data:
            out.append((event, json.loads(data)))
    return out


def test_index_and_health(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert "Java Coding Agent" in client.get("/").text


def test_chat_streams_text_and_run_started(client):
    res = client.post("/api/chat", json={"conversation_id": "c1", "message": "開發個人資料 CRUD"})
    assert res.status_code == 200 and "text/event-stream" in res.headers["content-type"]
    events = _sse_events(res.text)
    kinds = [e for e, _ in events]
    assert "run_started" in kinds and kinds[-1] == "done"
    assert "".join(d["content"] for e, d in events if e == "text") == "已啟動"
    run_id = next(d for e, d in events if e == "run_started")["data"]["run_id"]

    # Run 事件串流直到終態
    ev = client.get(f"/api/runs/{run_id}/events")
    run_events = _sse_events(ev.text)
    assert run_events[-1][0] == "done" and run_events[-1][1]["stage"] == "succeeded"
    assert client.get(f"/api/runs/{run_id}").json()["pull_request"].endswith("/pull/1")
    assert len(client.get("/api/runs").json()) == 1


def test_start_run_directly(client):
    res = client.post("/api/runs", json={"requirement": "需求"})
    assert res.status_code == 202 and res.json()["id"] == "run-1"
    assert client.get("/api/runs/nope").status_code == 404


def test_settings_endpoints(client):
    assert (
        client.put("/api/settings/llm", json={"model": "qwen3.6:35b-a3b-coding-mxfp8", "context_length": 32768}).json()[
            "context_length"
        ]
        == 32768
    )
    assert client.put("/api/settings/llm", json={"temperature": 9}).status_code == 400
    r = client.put("/api/settings/repo", json={"url": "https://github.com/acme/svc.git", "base_branch": "dev"}).json()
    assert r["full_name"] == "acme/svc" and r["base_branch"] == "dev"
    assert client.put("/api/settings/repo", json={"url": "ftp://x"}).status_code == 400


def test_system_prompt_endpoints(client):
    current = client.get("/api/system-prompt").json()
    assert current["version"] == 1 and current["is_default"]
    proposal = client.post(
        "/api/system-prompt/propose",
        json={"new_content": current["content"] + "\n- 加入 OpenAPI 註解", "summary": "OpenAPI"},
    ).json()
    assert client.get("/api/system-prompt").json()["version"] == 1  # 未確認
    confirmed = client.post(f"/api/system-prompt/confirm/{proposal['change_id']}").json()
    assert confirmed["version"] == 2 and "OpenAPI" in confirmed["content"]
    assert client.post("/api/system-prompt/confirm/bad").status_code == 400
    assert client.post("/api/system-prompt/reset").json()["is_default"]
    assert len(client.get("/api/system-prompt/history").json()) == 3
