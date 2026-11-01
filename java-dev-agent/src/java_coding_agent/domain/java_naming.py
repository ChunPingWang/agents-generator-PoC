"""由 repo 設定推導 Java 專案名稱與基底套件（確定性，不靠 LLM）。"""

from __future__ import annotations

import re

from java_coding_agent.domain.models import RepoSettings

_JAVA_KEYWORDS = {"abstract", "class", "default", "import", "int", "new", "package", "public", "static", "void"}


def _segment(raw: str) -> str:
    seg = re.sub(r"[^a-z0-9]", "", raw.lower())
    if not seg:
        seg = "app"
    if seg[0].isdigit() or seg in _JAVA_KEYWORDS:
        seg = f"x{seg}"
    return seg


def base_package_for(repo: RepoSettings) -> str:
    return f"com.github.{_segment(repo.owner)}.{_segment(repo.name)}"


def project_name_for(repo: RepoSettings) -> str:
    return re.sub(r"[^a-z0-9-]", "-", repo.name.lower()).strip("-") or "app"


def package_path(base_package: str) -> str:
    return base_package.replace(".", "/")
