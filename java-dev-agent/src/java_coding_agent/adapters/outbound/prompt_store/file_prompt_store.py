"""PromptStorePort 的本機 JSON 檔實作（Langfuse 不可用時的後備，也用於單元測試）。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from java_coding_agent.domain.models import SystemPromptVersion, utcnow


class FilePromptStore:
    def __init__(self, path: Path, default_content: str) -> None:
        self._path = path
        self._default = default_content
        if not self._path.exists():
            self._write([SystemPromptVersion(1, default_content, "預設", is_default=True)])

    def get_active(self) -> SystemPromptVersion:
        return self._read()[-1]

    def save_new_version(self, content: str, note: str) -> SystemPromptVersion:
        versions = self._read()
        version = SystemPromptVersion(len(versions) + 1, content, note, created_at=utcnow())
        self._write([*versions, version])
        return version

    def reset_to_default(self) -> SystemPromptVersion:
        versions = self._read()
        version = SystemPromptVersion(len(versions) + 1, self._default, "還原預設", is_default=True)
        self._write([*versions, version])
        return version

    def history(self) -> Sequence[SystemPromptVersion]:
        return self._read()

    def _read(self) -> list[SystemPromptVersion]:
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return [
            SystemPromptVersion(
                version=v["version"],
                content=v["content"],
                note=v.get("note", ""),
                created_at=datetime.fromisoformat(v["created_at"]),
                is_default=v.get("is_default", False),
            )
            for v in raw
        ]

    def _write(self, versions: list[SystemPromptVersion]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "version": v.version,
                "content": v.content,
                "note": v.note,
                "created_at": v.created_at.isoformat(),
                "is_default": v.is_default,
            }
            for v in versions
        ]
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
