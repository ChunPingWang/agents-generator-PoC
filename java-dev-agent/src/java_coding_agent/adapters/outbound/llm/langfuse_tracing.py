"""TracingPort 的 Langfuse 實作。未設定金鑰時退化為 no-op，不影響核心功能。"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

log = logging.getLogger(__name__)


class LangfuseTracing:
    def __init__(
        self, *, public_key: str | None, secret_key: str | None, host: str | None, enabled: bool = True
    ) -> None:
        self._client = None
        if enabled and public_key and secret_key:
            try:
                from langfuse import Langfuse

                self._client = Langfuse(public_key=public_key, secret_key=secret_key, host=host or None)
                log.info("Langfuse tracing 已啟用（%s）", host)
            except Exception as exc:  # pragma: no cover - 取決於環境
                log.warning("Langfuse 初始化失敗，改為 no-op：%s", exc)
        else:
            log.info("Langfuse 未設定金鑰，tracing 為 no-op")

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @property
    def client(self):
        return self._client

    def callbacks(self, *, trace_name: str, metadata: dict[str, Any] | None = None) -> list[BaseCallbackHandler]:
        if self._client is None:
            return []
        from langfuse.langchain import CallbackHandler

        return [CallbackHandler()]

    def flush(self) -> None:
        if self._client is not None:
            try:
                self._client.flush()
            except Exception as exc:  # pragma: no cover
                log.warning("Langfuse flush 失敗：%s", exc)
