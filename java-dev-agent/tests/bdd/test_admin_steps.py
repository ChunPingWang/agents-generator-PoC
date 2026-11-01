"""pytest-bdd 步驟：System Prompt 與 LLM 設定的對話管理（以 FakeToolChatModel 模擬 LLM 的工具選擇）。"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from pytest_bdd import given, parsers, scenarios, then, when

from java_coding_agent.application.chat_service import ChatService
from java_coding_agent.application.run_registry import RunRegistry
from java_coding_agent.application.settings_service import LlmSettingsService, RepoSettingsService
from java_coding_agent.application.system_prompt_service import SystemPromptService
from tests.unit.fakes import InMemoryConversations, InMemoryRuns, InMemorySettings, sequential_ids
from tests.unit.test_chat_service import FakeToolChatModel, StubFactory, tool_call

scenarios("system_prompt.feature", "llm_settings.feature")


class World:
    def __init__(self, prompt_store, templates, make_pipeline):
        self.settings = InMemorySettings()
        self.prompt_store = prompt_store
        self.templates = templates
        self.system_prompt = SystemPromptService(prompt_store, sequential_ids("chg"))
        self.registry = RunRegistry(
            pipeline_factory=lambda cb: make_pipeline(),
            run_repository=InMemoryRuns(),
            id_generator=sequential_ids("run"),
        )
        self.replies: list[str] = []

    def chat_with(self, responses):
        model = FakeToolChatModel(responses=responses)
        model.seen = []
        return ChatService(
            model_factory=StubFactory(model),
            llm_settings=LlmSettingsService(self.settings),
            repo_settings=RepoSettingsService(self.settings),
            system_prompt=self.system_prompt,
            runs=self.registry,
            conversations=InMemoryConversations(),
            templates=self.templates,
        )


@pytest.fixture
def world(prompt_store, templates, make_pipeline):
    w = World(prompt_store, templates, make_pipeline)
    yield w
    w.registry.shutdown()


def _plan_for(world: World, text: str):
    """模擬 LLM 對不同使用者語句的工具選擇（真實環境由 Qwen 決定）。"""
    current = world.prompt_store.get_active().content
    if "OpenAPI" in text:
        return [
            tool_call(
                "propose_system_prompt_change",
                new_content=current + "\n- 所有 REST API 都要有 OpenAPI 註解。",
                summary="加入 OpenAPI 註解規則",
            ),
            AIMessage(content="我將加入 OpenAPI 註解規則，請確認是否套用？"),
        ]
    if "還原" in text:
        return [tool_call("reset_system_prompt"), AIMessage(content="已還原預設")]
    if "模型改用" in text:
        return [
            tool_call("update_llm_settings", model="qwen3.6:35b-a3b-coding-mxfp8", context_length=32768),
            AIMessage(content="已更新模型與 context"),
        ]
    raise AssertionError(f"未預期的語句：{text}")


@given("System Prompt 已被修改過")
def prompt_modified(world):
    world.prompt_store.save_new_version("# 自訂規則 " + "x" * 80, "custom")


@when(parsers.parse('使用者說 "{text}"'))
def user_says(world, text):
    world.replies.append(world.chat_with(_plan_for(world, text)).reply("c1", text))


@then("Agent 回覆變更摘要並詢問確認")
def asks_confirmation(world):
    assert "確認" in world.replies[-1]
    assert len(world.system_prompt.pending()) == 1
    assert "OpenAPI" not in world.prompt_store.get_active().content


@when("使用者確認")
def user_confirms(world):
    change_id = world.system_prompt.pending()[0].change_id
    world.replies.append(
        world.chat_with(
            [tool_call("confirm_system_prompt_change", change_id=change_id), AIMessage(content="已套用")]
        ).reply("c1", "確認")
    )


@then(parsers.parse('System Prompt 產生新版本且內容包含 "{word}"'))
def new_version_contains(world, word):
    active = world.prompt_store.get_active()
    assert active.version == 2 and word in active.content


@then("System Prompt 內容等於出廠預設")
def prompt_is_default(world):
    active = world.prompt_store.get_active()
    assert active.is_default and active.content == world.templates.default_system_prompt()


@then(parsers.parse('LLM 設定的 model 為 "{model}"'))
def model_is(world, model):
    assert world.settings.llm.model == model


@then(parsers.parse("context length 為 {n:d}"))
def ctx_is(world, n):
    assert world.settings.llm.context_length == n
