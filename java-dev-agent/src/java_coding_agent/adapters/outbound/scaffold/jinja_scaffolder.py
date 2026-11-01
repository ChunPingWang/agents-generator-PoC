"""以 Jinja2 模板確定性地產生 Gradle 多模組六角形架構骨架。"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable

from jinja2 import Environment, StrictUndefined

from java_coding_agent.domain.java_naming import package_path
from java_coding_agent.domain.models import GeneratedFile

_TEMPLATE_PACKAGE = "java_coding_agent.adapters.outbound.scaffold.templates"
_SPECIAL_NAMES = {"gitignore.j2": ".gitignore"}


@dataclass(frozen=True)
class JavaStackVersions:
    spring_boot: str = "4.1.2"
    # 7.33+ 與 8.0.x 的 Runner.localeForPickle 改用 Locale.Builder，zh-TW 等含地區碼的語言會拋
    # IllformedLocaleException（cucumber-jvm PR #3117），故鎖定 7.32.0。
    cucumber: str = "7.32.0"
    archunit: str = "1.5.1"


class JinjaScaffolder:
    def __init__(self, versions: JavaStackVersions | None = None) -> None:
        self._versions = versions or JavaStackVersions()
        self._env = Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)

    def scaffold(self, project_name: str, base_package: str, feature_slug: str) -> list[GeneratedFile]:
        context = {
            "project_name": project_name,
            "base_package": base_package,
            "base_package_path": package_path(base_package),
            "feature_slug": feature_slug,
            "spring_boot_version": self._versions.spring_boot,
            "cucumber_version": self._versions.cucumber,
            "archunit_version": self._versions.archunit,
        }
        root = resources.files(_TEMPLATE_PACKAGE)
        files: list[GeneratedFile] = []
        for template_path, node in sorted(_walk(root, ""), key=lambda t: t[0]):
            if not template_path.endswith(".j2"):
                continue
            rendered = self._env.from_string(node.read_text(encoding="utf-8")).render(**context)
            out_path = _SPECIAL_NAMES.get(template_path, template_path[: -len(".j2")])
            out_path = out_path.replace("__pkg__", context["base_package_path"])
            files.append(GeneratedFile(path=out_path, content=rendered))
        return files


def _walk(node: Traversable, prefix: str):
    for child in node.iterdir():
        name = child.name
        if name in {"__init__.py", "__pycache__"}:
            continue
        rel = f"{prefix}{name}" if not prefix else f"{prefix}/{name}"
        if child.is_dir():
            yield from _walk(child, rel)
        else:
            yield rel, child
