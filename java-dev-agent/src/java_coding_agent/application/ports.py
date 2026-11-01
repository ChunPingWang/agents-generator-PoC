"""Application 層自己需要的 ports（與 LangChain 型別有關，所以不放在 domain）。"""

from __future__ import annotations

from typing import Any, Protocol

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel

from java_coding_agent.domain.models import LlmSettings


class ChatModelFactory(Protocol):
    def create(self, settings: LlmSettings, *, effort: str = "normal") -> BaseChatModel: ...


class TracingPort(Protocol):
    """提供 LangChain callbacks（例如 Langfuse CallbackHandler）；不可用時回傳空清單。"""

    def callbacks(self, *, trace_name: str, metadata: dict[str, Any] | None = None) -> list[BaseCallbackHandler]: ...

    def flush(self) -> None: ...
