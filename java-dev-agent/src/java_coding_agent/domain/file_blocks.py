"""解析 LLM 輸出中的多檔案區塊。

格式（對小型本機模型最穩定）：

    === FILE: path/to/File.java ===
    ...content...
    === END FILE ===

允許 content 以 ``` fenced code block 包住；解析時會剝除最外層 fence。
刪除檔案使用單行區塊：``=== DELETE FILE: path/to/File.java ===``。
"""

from __future__ import annotations

import re

from java_coding_agent.domain.errors import LlmOutputError
from java_coding_agent.domain.java_paths import (
    dedupe_step_definitions,
    ensure_adapter_components,
    ensure_jakarta_imports,
    ensure_mockito_extension,
    ensure_test_static_imports,
    ensure_valid_on_request_body,
    fix_inject_mocks_initializers,
    fix_null_http_entity,
    fix_record_compact_constructors,
    fix_response_entity_arg_order,
    flatten_adapter_subpackages,
    is_cucumber_glue,
    normalize_boot_tests,
    normalize_cucumber_steps,
    normalize_data_jpa_tests,
    normalize_gherkin_zh_tw,
    normalize_java_path,
    publicize_adapter_types,
    publicize_cucumber_steps,
    relax_mockito_strictness,
    rewrite_legacy_imports,
    rewrite_removed_apis,
    strip_cause_assertions,
    strip_cucumber_step_stereotypes,
    strip_generated_value,
    strip_spring_stereotypes,
    truncate_after_thrown_assertion,
)
from java_coding_agent.domain.models import FileDeletion, GeneratedFile

_HEADER = re.compile(r"^[ \t]*===\s*(?P<kind>DELETE FILE|FILE):\s*(?P<path>[^\n=]+?)\s*===[ \t]*$", re.M)
_END = re.compile(r"^[ \t]*===\s*END FILE\s*===[ \t]*$", re.M)
_FENCE = re.compile(r"^```[\w+-]*\s*\n(?P<inner>.*?)\n```\s*$", re.DOTALL)


def parse_file_blocks(text: str) -> list[GeneratedFile]:
    """只回傳要寫入的檔案；Java 檔案路徑會依 package 宣告自動校正。

    `=== END FILE ===` 可省略（小模型常漏寫）：區塊在下一個 `=== FILE:` / `=== DELETE FILE:` 標頭或文末結束。
    """
    headers = list(_HEADER.finditer(text))
    files: list[GeneratedFile] = []
    for idx, header in enumerate(headers):
        if header.group("kind") != "FILE":
            continue
        end = headers[idx + 1].start() if idx + 1 < len(headers) else len(text)
        body = text[header.end() : end]
        end_marker = _END.search(body)
        if end_marker:
            body = body[: end_marker.start()]
        body = body.strip("\n")
        fenced = _FENCE.match(body.strip())
        if fenced:
            body = fenced.group("inner")
        content = body.rstrip() + "\n"
        content = rewrite_legacy_imports(header.group("path"), content)
        content = fix_null_http_entity(header.group("path"), content)
        content = rewrite_removed_apis(header.group("path"), content)
        content = fix_record_compact_constructors(header.group("path"), content)
        content = fix_response_entity_arg_order(header.group("path"), content)
        path = normalize_java_path(header.group("path").strip().strip("`"), content)
        if path.endswith(".java") and "/src/test/" in path and is_cucumber_glue(content):
            content = normalize_cucumber_steps(content)
            content = dedupe_step_definitions(path, content)
            content = publicize_cucumber_steps(path, content)
            content = strip_cucumber_step_stereotypes(path, content)
        content = normalize_gherkin_zh_tw(path, content)
        content = strip_spring_stereotypes(path, content)
        content = publicize_adapter_types(path, content)
        content = strip_generated_value(path, content)
        content = ensure_jakarta_imports(path, content)
        content = ensure_adapter_components(path, content)
        content = ensure_valid_on_request_body(path, content)
        content = ensure_test_static_imports(path, content)
        content = fix_inject_mocks_initializers(path, content)
        content = ensure_mockito_extension(path, content)
        content = relax_mockito_strictness(path, content)
        content = normalize_data_jpa_tests(path, content)
        content = truncate_after_thrown_assertion(path, content)
        content = strip_cause_assertions(path, content)
        content = normalize_boot_tests(path, content)
        files.append(GeneratedFile(path=path, content=content))
    if not files and not any(h.group("kind") == "DELETE FILE" for h in headers):
        raise LlmOutputError("輸出中找不到任何 `=== FILE: ... ===` 區塊")
    return flatten_adapter_subpackages(files)


def parse_deletions(text: str) -> list[FileDeletion]:
    return [
        FileDeletion(path=m.group("path").strip().strip("`"))
        for m in _HEADER.finditer(text)
        if m.group("kind") == "DELETE FILE"
    ]


def render_file_blocks(files: list[GeneratedFile]) -> str:
    """反向操作，用於把既有檔案回饋給 LLM。"""
    parts = [f"=== FILE: {f.path} ===\n{f.content.rstrip()}\n=== END FILE ===" for f in files]
    return "\n\n".join(parts)
