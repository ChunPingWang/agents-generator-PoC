import pytest

from java_coding_agent.domain.errors import LlmOutputError
from java_coding_agent.domain.tasks_md import parse_tasks_md, render_tasks_md
from tests.unit.fakes import TASKS_OUTPUT


def test_parses_tasks_with_description_and_test():
    body = TASKS_OUTPUT.split("===\n", 1)[1].rsplit("=== END", 1)[0]
    tasks = parse_tasks_md(body)
    assert [t.id for t in tasks] == ["T1", "T2"]
    assert tasks[0].title == "建立 Profile 領域模型"
    assert "Profile.java" in tasks[0].description
    assert "ProfileTest.java" in tasks[0].test_hint


def test_multiline_description_is_joined():
    md = "## T1 標題\n- 說明：第一行\n  第二行\n- 測試：測試說明\n"
    t = parse_tasks_md(md)[0]
    assert t.description == "第一行 第二行"
    assert t.test_hint == "測試說明"


def test_raises_without_tasks():
    with pytest.raises(LlmOutputError):
        parse_tasks_md("# nothing here")


def test_render_marks_done():
    tasks = parse_tasks_md("## T1 a\n- 說明：d\n- 測試：t\n")
    rendered = render_tasks_md("x", [tasks[0].completed()])
    assert "- [x] 完成" in rendered
