"""解析 SDD tasks.md（LLM 輸出）為 TaskItem 清單。"""

from __future__ import annotations

import re

from java_coding_agent.domain.errors import LlmOutputError
from java_coding_agent.domain.models import TaskItem

_HEADER = re.compile(r"^##\s+(?P<id>T\d+)\s+(?P<title>.+?)\s*$", re.M)
_FIELD = re.compile(r"^-\s*(?P<key>說明|測試|Description|Test)\s*[:：]\s*(?P<value>.*)$", re.M)


def parse_tasks_md(text: str) -> list[TaskItem]:
    headers = list(_HEADER.finditer(text))
    if not headers:
        raise LlmOutputError("tasks.md 中找不到 `## Tn <標題>` 任務段落")
    tasks: list[TaskItem] = []
    for idx, header in enumerate(headers):
        end = headers[idx + 1].start() if idx + 1 < len(headers) else len(text)
        section = text[header.end() : end]
        fields = {m.group("key"): m.group("value").strip() for m in _FIELD.finditer(section)}
        # 支援多行說明：把欄位後直到下一欄位的縮排行併入
        description = _collect(section, ("說明", "Description"))
        test_hint = _collect(section, ("測試", "Test"))
        tasks.append(
            TaskItem(
                id=header.group("id"),
                title=header.group("title").strip(),
                description=description or fields.get("說明", fields.get("Description", "")),
                test_hint=test_hint or fields.get("測試", fields.get("Test", "")),
            )
        )
    return tasks


def _collect(section: str, keys: tuple[str, ...]) -> str:
    lines = section.splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        m = _FIELD.match(line)
        if m:
            if m.group("key") in keys:
                capturing = True
                out.append(m.group("value").strip())
            else:
                capturing = False
            continue
        if capturing and line.strip() and (line.startswith("  ") or line.startswith("\t")):
            out.append(line.strip())
        elif capturing and not line.strip():
            continue
        elif capturing:
            capturing = False
    return " ".join(out).strip()


def render_tasks_md(title: str, tasks: list[TaskItem]) -> str:
    parts = [f"# Tasks — {title}", ""]
    for t in tasks:
        mark = "x" if t.done else " "
        parts.append(f"## {t.id} {t.title}")
        parts.append(f"- [{mark}] 完成")
        parts.append(f"- 說明：{t.description}")
        parts.append(f"- 測試：{t.test_hint}")
        parts.append("")
    return "\n".join(parts)
