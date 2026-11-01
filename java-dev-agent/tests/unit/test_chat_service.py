"""ChatService 以 FakeToolChatModel 驗證工具路由；不需要真實 LLM。"""

from __future__ import annotations

import json
from collections import deque
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from java_coding_agent.application.chat_service import ChatService
from java_coding_agent.application.run_registry import RunRegistry
from java_coding_agent.application.settings_service import LlmSettingsService, RepoSettingsService
from java_coding_agent.application.system_prompt_service import SystemPromptService
from java_coding_agent.domain.models import LlmSettings, RunStage
from tests.unit.fakes import InMemoryConversations, InMemoryRuns, InMemorySettings, sequential_ids


class FakeToolChatModel(BaseChatModel):
    """回放預先排好的 AIMessage；每次 _generate 取一個。"""

    responses: list[AIMessage]
    seen: list[list[BaseMessage]] = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        queue: deque = self.__dict__.setdefault("_queue", deque(self.responses))
        msg = queue.popleft() if len(queue) > 1 else queue[0]
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kwargs):
        self.__dict__["_bound_tools"] = [getattr(t, "name", str(t)) for t in tools]
        return self

    @property
    def _llm_type(self) -> str:
        return "fake-tool"


def tool_call(name: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": f"call-{name}", "name": name, "args": args, "type": "tool_call"}])


class StubFactory:
    def __init__(self, model):
        self.model = model
        self.settings_seen: list[LlmSettings] = []

    def create(self, settings, *, effort="normal"):
        self.settings_seen.append(settings)
        return self.model


@pytest.fixture
def world(make_pipeline, prompt_store, templates):
    settings = InMemorySettings()
    runs_repo = InMemoryRuns()

    def factory(on_event):
        p = make_pipeline()
        p._on_event = on_event
        return p

    registry = RunRegistry(pipeline_factory=factory, run_repository=runs_repo, id_generator=sequential_ids("run"))

    def make(responses: list[AIMessage]):
        model = FakeToolChatModel(responses=responses)
        model.seen = []
        factory_ = StubFactory(model)
        svc = ChatService(
            model_factory=factory_,
            llm_settings=LlmSettingsService(settings),
            repo_settings=RepoSettingsService(settings),
            system_prompt=SystemPromptService(prompt_store, sequential_ids("chg")),
            runs=registry,
            conversations=InMemoryConversations(),
            templates=templates,
        )
        return svc, model, factory_

    yield make, settings, registry, prompt_store
    registry.shutdown()


def test_business_requirement_starts_feature_run(world):
    make, _, registry, _ = world
    svc, model, _ = make(
        [tool_call("start_feature_run", business_requirement="個人資料 CRUD"), AIMessage(content="已啟動")]
    )
    chunks = list(svc.stream("c1", "開發一個 backend 服務，可以新增、修改、刪除、查詢的個人資料服務"))
    started = [c for c in chunks if c.kind == "run_started"]
    assert len(started) == 1 and started[0].data["run_id"] == "run-1"
    assert "".join(c.content for c in chunks if c.kind == "text") == "已啟動"
    run = registry.get("run-1")
    deadline = __import__("time").time() + 10
    while not run.stage.is_terminal and __import__("time").time() < deadline:
        __import__("time").sleep(0.02)
    assert run.stage is RunStage.SUCCEEDED


def test_update_llm_settings_via_conversation(world):
    make, settings, _, _ = world
    svc, _, _ = make(
        [
            tool_call("update_llm_settings", model="qwen3.6:35b-a3b-coding-mxfp8", context_length=32768),
            AIMessage(content="好的"),
        ]
    )
    svc.reply("c1", "模型改用 qwen3.6:35b-a3b-coding-mxfp8，context 設為 32768")
    assert settings.llm.model == "qwen3.6:35b-a3b-coding-mxfp8"
    assert settings.llm.context_length == 32768


def test_system_prompt_change_requires_confirmation(world):
    make, _, _, prompt_store = world
    new_content = prompt_store.get_active().content + "\n- 所有 REST API 都要有 OpenAPI 註解。"
    svc, _, _ = make(
        [
            tool_call("propose_system_prompt_change", new_content=new_content, summary="加入 OpenAPI 規則"),
            AIMessage(content="請確認"),
        ]
    )
    svc.reply("c1", "請把規則改成所有 REST API 都要有 OpenAPI 註解")
    assert "OpenAPI" not in prompt_store.get_active().content  # 尚未確認

    svc2, _, _ = make([tool_call("confirm_system_prompt_change", change_id="chg-1"), AIMessage(content="已套用")])
    svc2._system_prompt = svc._system_prompt  # 同一個 service 保存 pending
    svc2.reply("c1", "確認")
    assert "OpenAPI" in prompt_store.get_active().content
    assert prompt_store.get_active().version == 2


def test_reset_system_prompt(world):
    make, _, _, prompt_store = world
    prompt_store.save_new_version("# 自訂 " + "x" * 60, "custom")
    svc, _, _ = make([tool_call("reset_system_prompt"), AIMessage(content="已還原")])
    svc.reply("c1", "還原預設的 System Prompt")
    assert prompt_store.get_active().is_default


def test_conversation_history_is_persisted_and_replayed(world):
    make, _, _, _ = world
    svc, model, _ = make([AIMessage(content="你好")])
    svc.reply("c1", "嗨")
    svc.reply("c1", "再嗨")
    assert len(svc._conversations.get("c1").messages) == 4
    # 第二輪送進模型的訊息應包含第一輪的使用者與助理訊息
    assert any(getattr(m, "content", "") == "你好" for m in model.seen[-1])


def test_domain_error_from_tool_is_reported_not_raised(world):
    make, _, _, _ = world
    svc, _, _ = make([tool_call("update_llm_settings", temperature=9.0), AIMessage(content="完成")])
    text = svc.reply("c1", "temperature 設 9")
    assert "完成" in text or "失敗" in text


def test_model_is_created_from_current_settings(world):
    make, settings, _, _ = world
    svc, _, factory = make([AIMessage(content="ok")])
    svc.reply("c1", "hi")
    assert factory.settings_seen == [settings.llm]


def test_tools_are_bound(world):
    make, _, _, _ = world
    svc, model, _ = make([AIMessage(content="ok")])
    svc.reply("c1", "hi")
    assert "start_feature_run" in model.__dict__["_bound_tools"]
    assert json.dumps(model.__dict__["_bound_tools"])  # serializable names
