"""依 LlmSettings 建立 LangChain chat model（Ollama / OpenAI 相容 / Anthropic）。"""

from __future__ import annotations

import os

from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from java_coding_agent.domain.errors import ValidationError
from java_coding_agent.domain.models import LlmProvider, LlmSettings


class LangChainModelFactory:
    def __init__(
        self, request_timeout_seconds: float = 600.0, max_output_tokens: int = 24000, thinking: bool = False
    ) -> None:
        self._timeout = request_timeout_seconds
        self._max_output = max_output_tokens
        self._thinking = thinking
        """Ollama 推理模型的 thinking：思考 token 會消耗 num_predict 額度，長任務下常導致沒有輸出內容，預設關閉。"""

    def create(self, settings: LlmSettings, *, effort: str = "normal") -> BaseChatModel:
        if settings.provider is LlmProvider.OLLAMA:
            from langchain_ollama import ChatOllama

            return ChatOllama(
                model=settings.model,
                base_url=settings.base_url,
                temperature=settings.temperature,
                num_ctx=settings.context_length,
                seed=settings.seed,
                # 防止模型無限產生；thinking 的 token 也算在 num_predict 內，高推理模式給三倍額度
                num_predict=self._max_output * (3 if effort == "high" else 1),
                reasoning=self._thinking or effort == "high",  # 卡住的修正才啟用 thinking
                repeat_penalty=1.15,  # temperature 0 時模型偶爾陷入重複註解迴圈直到額度用盡，加重重複懲罰
                repeat_last_n=256,
                keep_alive="30m",
                client_kwargs={"timeout": self._timeout},
            )
        if settings.provider is LlmProvider.OPENAI:
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=settings.model,
                base_url=settings.base_url if "openai.com" not in settings.base_url else None,
                temperature=settings.temperature,
                api_key=SecretStr(_api_key(settings, "OPENAI_API_KEY")),
                timeout=self._timeout,
                seed=settings.seed,
            )
        if settings.provider is LlmProvider.ANTHROPIC:
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(  # type: ignore[call-arg]
                model_name=settings.model,
                temperature=settings.temperature,
                api_key=SecretStr(_api_key(settings, "ANTHROPIC_API_KEY")),
                timeout=self._timeout,
                max_tokens_to_sample=min(self._max_output, 32000),
            )
        raise ValidationError(f"不支援的 provider：{settings.provider}")


def _api_key(settings: LlmSettings, default_env: str) -> str:
    env_name = settings.api_key_env or default_env
    value = os.environ.get(env_name)
    if not value:
        raise ValidationError(f"環境變數 {env_name} 未設定，無法使用 provider {settings.provider.value}")
    return value
