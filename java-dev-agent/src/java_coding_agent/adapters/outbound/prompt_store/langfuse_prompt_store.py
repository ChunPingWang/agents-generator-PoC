"""PromptStorePort 的 Langfuse Prompt Management 實作：每次修改建立新版本並標為 production。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from java_coding_agent.domain.models import SystemPromptVersion, utcnow

log = logging.getLogger(__name__)


class LangfusePromptStore:
    def __init__(self, client: Any, default_content: str, prompt_name: str = "java-coding-rules") -> None:
        self._client = client
        self._default = default_content
        self._name = prompt_name
        self._ensure_exists()

    def _ensure_exists(self) -> None:
        try:
            self._client.get_prompt(self._name, label="production", cache_ttl_seconds=0)
        except Exception:
            log.info("Langfuse 中沒有 prompt %s，建立預設版本", self._name)
            self._client.create_prompt(
                name=self._name,
                prompt=self._default,
                labels=["production"],
                type="text",
                config={"is_default": True, "note": "預設"},
                commit_message="預設 Java 程式碼產生規則",
            )

    def get_active(self) -> SystemPromptVersion:
        p = self._client.get_prompt(self._name, label="production", cache_ttl_seconds=0)
        return self._to_version(p)

    def save_new_version(self, content: str, note: str) -> SystemPromptVersion:
        p = self._client.create_prompt(
            name=self._name,
            prompt=content,
            labels=["production"],
            type="text",
            config={"is_default": False, "note": note},
            commit_message=note,
        )
        return self._to_version(p)

    def reset_to_default(self) -> SystemPromptVersion:
        p = self._client.create_prompt(
            name=self._name,
            prompt=self._default,
            labels=["production"],
            type="text",
            config={"is_default": True, "note": "還原預設"},
            commit_message="還原預設",
        )
        return self._to_version(p)

    def history(self) -> Sequence[SystemPromptVersion]:
        active = self.get_active()
        versions: list[SystemPromptVersion] = []
        for n in range(1, active.version + 1):
            try:
                versions.append(self._to_version(self._client.get_prompt(self._name, version=n, cache_ttl_seconds=0)))
            except Exception:  # 版本可能被刪除
                continue
        return versions

    @staticmethod
    def _to_version(p: Any) -> SystemPromptVersion:
        config = getattr(p, "config", None) or {}
        created = getattr(p, "created_at", None) or getattr(p, "createdAt", None)
        created_at = created if isinstance(created, datetime) else utcnow()
        return SystemPromptVersion(
            version=int(getattr(p, "version", 0)),
            content=str(getattr(p, "prompt", "")),
            note=str(config.get("note", "")),
            created_at=created_at,
            is_default=bool(config.get("is_default", False)),
        )
