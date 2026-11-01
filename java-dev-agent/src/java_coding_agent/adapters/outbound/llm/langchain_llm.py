"""LlmPort 的 LangChain 實作：每次呼叫取用目前 LLM 設定，附上 Langfuse callbacks。"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from java_coding_agent.application.ports import ChatModelFactory, TracingPort
from java_coding_agent.domain.errors import LlmOutputError
from java_coding_agent.domain.models import LlmSettings
from java_coding_agent.domain.ports import SettingsRepository

log = logging.getLogger(__name__)


class LangChainLlm:
    """LlmPort 實作。

    `call_timeout_seconds` 是整次呼叫的硬性上限（與 HTTP read timeout 不同）：本機模型偶爾會卡住或無限產生，
    超時即丟 LlmOutputError，讓 SDD 流程以「輸出無效」的方式重試，而不是永遠等待。
    """

    def __init__(
        self,
        factory: ChatModelFactory,
        settings: SettingsRepository,
        tracing: TracingPort,
        *,
        call_timeout_seconds: float = 900.0,
    ) -> None:
        self._factory = factory
        self._settings = settings
        self._tracing = tracing
        self._cached: dict[str, tuple[LlmSettings, BaseChatModel]] = {}
        self._timeout = call_timeout_seconds
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm-call")

    def _model(self, effort: str = "normal") -> BaseChatModel:
        current = self._settings.load_llm_settings()
        cached = self._cached.get(effort)
        if cached is None or cached[0] != current:
            cached = (current, self._factory.create(current, effort=effort))
            self._cached[effort] = cached
        return cached[1]

    def _config(self, name: str, metadata: dict[str, Any] | None) -> RunnableConfig:
        return RunnableConfig(
            callbacks=self._tracing.callbacks(trace_name=name or "llm", metadata=metadata),
            run_name=name or "llm",
            metadata=metadata or {},
        )

    def complete(
        self,
        system: str,
        user: str,
        *,
        name: str = "",
        metadata: dict[str, Any] | None = None,
        effort: str = "normal",
    ) -> str:
        model = self._model(effort)
        messages = [SystemMessage(content=system), HumanMessage(content=user)]
        config = self._config(name, metadata)
        future = self._executor.submit(model.invoke, messages, config=config)
        try:
            response = future.result(timeout=self._timeout)
        except FutureTimeoutError as exc:
            future.cancel()
            log.warning("LLM 呼叫 %s 超過 %.0f 秒未完成", name, self._timeout)
            raise LlmOutputError(f"LLM 呼叫 {name or 'llm'} 超過 {self._timeout:.0f} 秒未完成") from exc
        return _text(response.content)

    def stream(self, system: str, user: str, *, name: str = "") -> Iterator[str]:
        for chunk in self._model().stream(
            [SystemMessage(content=system), HumanMessage(content=user)], config=self._config(name, None)
        ):
            text = _text(chunk.content)
            if text:
                yield text


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            (
                b.get("text", "")
                if isinstance(b, dict) and b.get("type") == "text"
                else (b if isinstance(b, str) else "")
            )
            for b in content
        )
    return str(content)
