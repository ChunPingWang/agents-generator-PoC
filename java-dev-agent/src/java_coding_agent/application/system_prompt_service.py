"""US-2：以對話查看、提議、確認、還原 System Prompt。"""

from __future__ import annotations

from collections.abc import Sequence

from java_coding_agent.domain.errors import ValidationError
from java_coding_agent.domain.models import PendingPromptChange, SystemPromptVersion
from java_coding_agent.domain.ports import IdGenerator, PromptStorePort


class SystemPromptService:
    def __init__(self, store: PromptStorePort, id_generator: IdGenerator) -> None:
        self._store = store
        self._new_id = id_generator
        self._pending: dict[str, PendingPromptChange] = {}

    def current(self) -> SystemPromptVersion:
        return self._store.get_active()

    def history(self) -> Sequence[SystemPromptVersion]:
        return self._store.history()

    def propose(self, new_content: str, summary: str) -> PendingPromptChange:
        content = new_content.strip()
        if len(content) < 50:
            raise ValidationError("System Prompt 內容過短（少於 50 字元），請提供完整內容而非片段")
        if content == self.current().content.strip():
            raise ValidationError("提議的內容與目前版本相同")
        change = PendingPromptChange(change_id=self._new_id(), new_content=content, summary=summary.strip())
        self._pending[change.change_id] = change
        return change

    def confirm(self, change_id: str) -> SystemPromptVersion:
        change = self._pending.pop(change_id, None)
        if change is None:
            raise ValidationError(f"找不到待確認的變更 {change_id}，請重新提出")
        return self._store.save_new_version(change.new_content, note=change.summary)

    def discard(self, change_id: str) -> None:
        self._pending.pop(change_id, None)

    def pending(self) -> Sequence[PendingPromptChange]:
        return list(self._pending.values())

    def reset(self) -> SystemPromptVersion:
        self._pending.clear()
        return self._store.reset_to_default()
