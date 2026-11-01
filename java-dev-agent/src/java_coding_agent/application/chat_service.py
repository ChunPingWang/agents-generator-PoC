"""對話層：LangChain tool-calling agent。

工具涵蓋 System Prompt 管理、LLM 設定、目標 repo 設定與啟動 SDD Run。
產碼本身不在對話中進行，而是交給 RunRegistry 於背景執行。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from java_coding_agent.application.ports import ChatModelFactory
from java_coding_agent.application.run_registry import RunRegistry
from java_coding_agent.application.settings_service import LlmSettingsService, RepoSettingsService
from java_coding_agent.application.system_prompt_service import SystemPromptService
from java_coding_agent.application.templates import PromptTemplates
from java_coding_agent.domain.errors import DomainError
from java_coding_agent.domain.models import Conversation, Role
from java_coding_agent.domain.ports import ConversationRepository


@dataclass(frozen=True)
class ChatChunk:
    """串流片段：text 為回覆文字；tool 為工具呼叫事件（給 UI 顯示用）。"""

    kind: str  # "text" | "tool" | "run_started"
    content: str
    data: dict[str, Any] | None = None


class ChatService:
    def __init__(
        self,
        *,
        model_factory: ChatModelFactory,
        llm_settings: LlmSettingsService,
        repo_settings: RepoSettingsService,
        system_prompt: SystemPromptService,
        runs: RunRegistry,
        conversations: ConversationRepository,
        templates: PromptTemplates,
        callbacks_provider: Callable[[], list[BaseCallbackHandler]] | None = None,
    ) -> None:
        self._model_factory = model_factory
        self._llm_settings = llm_settings
        self._repo_settings = repo_settings
        self._system_prompt = system_prompt
        self._runs = runs
        self._conversations = conversations
        self._templates = templates
        self._callbacks_provider = callbacks_provider or (lambda: [])

    # ------------------------------------------------------------------ tools
    def build_tools(self) -> list:
        svc_prompt, svc_llm, svc_repo, runs = self._system_prompt, self._llm_settings, self._repo_settings, self._runs

        @tool
        def show_system_prompt() -> str:
            """顯示目前生效的 Java 程式碼產生規則（System Prompt）與版本。"""
            v = svc_prompt.current()
            return f"版本 {v.version}（{'預設' if v.is_default else v.note}）\n\n{v.content}"

        @tool
        def propose_system_prompt_change(new_content: str, summary: str) -> str:
            """提出 System Prompt 變更提案。new_content 必須是修改後的完整 System Prompt 全文，summary 為一句變更摘要。
            回傳 change_id；需使用者確認後再呼叫 confirm_system_prompt_change。"""
            change = svc_prompt.propose(new_content, summary)
            return json.dumps({"change_id": change.change_id, "summary": change.summary}, ensure_ascii=False)

        @tool
        def confirm_system_prompt_change(change_id: str) -> str:
            """使用者確認後套用指定 change_id 的 System Prompt 變更，建立新版本。"""
            v = svc_prompt.confirm(change_id)
            return f"已套用，新版本 {v.version}：{v.note}"

        @tool
        def reset_system_prompt() -> str:
            """將 System Prompt 還原為出廠預設。"""
            v = svc_prompt.reset()
            return f"已還原為預設（版本 {v.version}）"

        @tool
        def show_llm_settings() -> str:
            """顯示目前 LLM 設定（provider、base_url、model、temperature、context_length）。"""
            s = svc_llm.current()
            return json.dumps(
                {
                    "provider": s.provider.value,
                    "base_url": s.base_url,
                    "model": s.model,
                    "temperature": s.temperature,
                    "context_length": s.context_length,
                },
                ensure_ascii=False,
            )

        @tool
        def update_llm_settings(
            provider: str | None = None,
            base_url: str | None = None,
            model: str | None = None,
            temperature: float | None = None,
            context_length: int | None = None,
        ) -> str:
            """更新 LLM 設定；只需傳入要修改的欄位。provider 可為 ollama、openai、anthropic。"""
            s = svc_llm.update(
                provider=provider,
                base_url=base_url,
                model=model,
                temperature=temperature,
                context_length=context_length,
            )
            return (
                f"已更新：provider={s.provider.value} model={s.model} base_url={s.base_url} "
                f"temperature={s.temperature} context_length={s.context_length}"
            )

        @tool
        def show_repo_settings() -> str:
            """顯示目標 GitHub repo 設定。"""
            r = svc_repo.current()
            return f"url={r.url} base_branch={r.base_branch}"

        @tool
        def update_repo_settings(url: str | None = None, base_branch: str | None = None) -> str:
            """更新產出程式碼要推送的 GitHub repo URL 與 base branch。"""
            r = svc_repo.update(url=url, base_branch=base_branch)
            return f"已更新：url={r.url} base_branch={r.base_branch}"

        @tool
        def start_feature_run(business_requirement: str) -> str:
            """以使用者的業務需求啟動 SDD 流程（spec→plan→tasks→TDD 實作→驗證→PR），於背景執行，回傳 run_id。"""
            run = runs.start(business_requirement)
            return json.dumps({"run_id": run.id, "status": run.stage.value}, ensure_ascii=False)

        return [
            show_system_prompt,
            propose_system_prompt_change,
            confirm_system_prompt_change,
            reset_system_prompt,
            show_llm_settings,
            update_llm_settings,
            show_repo_settings,
            update_repo_settings,
            start_feature_run,
        ]

    # ------------------------------------------------------------------ chat
    def _agent(self):
        model = self._model_factory.create(self._llm_settings.current())
        return create_agent(model, self.build_tools(), system_prompt=self._templates.chat_agent_instructions())

    def _load(self, conversation_id: str) -> Conversation:
        return self._conversations.get(conversation_id) or Conversation(id=conversation_id)

    @staticmethod
    def _to_lc(conversation: Conversation) -> list[BaseMessage]:
        out: list[BaseMessage] = []
        for m in conversation.messages:
            if m.role is Role.USER:
                out.append(HumanMessage(content=m.content))
            elif m.role is Role.ASSISTANT:
                out.append(AIMessage(content=m.content))
        return out

    def stream(self, conversation_id: str, user_text: str) -> Iterator[ChatChunk]:
        conversation = self._load(conversation_id)
        conversation.add(Role.USER, user_text)
        history = self._to_lc(conversation)
        agent = self._agent()
        answer_parts: list[str] = []
        try:
            for item, _meta in agent.stream(
                {"messages": history},
                stream_mode="messages",
                config={"callbacks": self._callbacks_provider(), "metadata": {"conversation_id": conversation_id}},
            ):
                if isinstance(item, AIMessage):  # AIMessageChunk 亦為其子類別
                    text = _text_of(item)
                    if text:
                        answer_parts.append(text)
                        yield ChatChunk("text", text)
                    for tc in item.tool_calls or []:
                        if tc.get("name"):
                            yield ChatChunk(
                                "tool", f"呼叫工具 {tc['name']}", {"name": tc["name"], "args": tc.get("args")}
                            )
                elif isinstance(item, ToolMessage):
                    content = (
                        item.content if isinstance(item.content, str) else json.dumps(item.content, ensure_ascii=False)
                    )
                    yield ChatChunk("tool_result", content, {"name": item.name})
                    if item.name == "start_feature_run":
                        try:
                            yield ChatChunk("run_started", content, json.loads(content))
                        except json.JSONDecodeError:
                            pass
        except DomainError as exc:
            msg = f"操作失敗：{exc}"
            answer_parts.append(msg)
            yield ChatChunk("text", msg)
        final = "".join(answer_parts).strip()
        if final:
            conversation.add(Role.ASSISTANT, final)
        self._conversations.save(conversation)

    def reply(self, conversation_id: str, user_text: str) -> str:
        return "".join(c.content for c in self.stream(conversation_id, user_text) if c.kind == "text")


def _text_of(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif isinstance(block, str):
            parts.append(block)
    return "".join(parts)
