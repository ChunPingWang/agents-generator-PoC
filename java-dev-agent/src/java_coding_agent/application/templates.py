"""載入與渲染 prompt 模板（Jinja2），模板檔隨套件發佈。"""

from __future__ import annotations

from importlib import resources
from typing import Any

from jinja2 import Environment, StrictUndefined

_PACKAGE = "java_coding_agent.application.prompts"


class PromptTemplates:
    def __init__(self) -> None:
        self._env = Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)
        self._cache: dict[str, str] = {}

    def raw(self, name: str) -> str:
        if name not in self._cache:
            self._cache[name] = resources.files(_PACKAGE).joinpath(name).read_text(encoding="utf-8")
        return self._cache[name]

    def render(self, name: str, **context: Any) -> str:
        return self._env.from_string(self.raw(name)).render(**context)

    def default_system_prompt(self) -> str:
        return self.raw("system_prompt.default.md")

    def chat_agent_instructions(self) -> str:
        return self.raw("chat_agent.md")
