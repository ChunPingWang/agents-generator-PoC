import pytest

from java_coding_agent.application.system_prompt_service import SystemPromptService
from java_coding_agent.domain.errors import ValidationError
from tests.unit.fakes import InMemoryPromptStore, sequential_ids

DEFAULT = "# 預設規則\n" + "x" * 100
NEW = "# 新規則\n所有 REST API 都要有 OpenAPI 註解。" + "y" * 100


@pytest.fixture
def service():
    return SystemPromptService(InMemoryPromptStore(DEFAULT), sequential_ids("chg"))


def test_propose_does_not_apply_until_confirmed(service):
    change = service.propose(NEW, "加入 OpenAPI 規則")
    assert service.current().content == DEFAULT
    assert [c.change_id for c in service.pending()] == [change.change_id]


def test_confirm_creates_new_version_with_note(service):
    change = service.propose(NEW, "加入 OpenAPI 規則")
    v = service.confirm(change.change_id)
    assert v.version == 2
    assert "OpenAPI" in service.current().content
    assert service.current().note == "加入 OpenAPI 規則"
    assert service.pending() == []


def test_confirm_unknown_change_fails(service):
    with pytest.raises(ValidationError):
        service.confirm("nope")


def test_reset_restores_default_as_new_version(service):
    service.confirm(service.propose(NEW, "n").change_id)
    v = service.reset()
    assert v.is_default and v.content == DEFAULT and v.version == 3
    assert len(service.history()) == 3


@pytest.mark.parametrize("content", ["太短", DEFAULT])
def test_propose_rejects_short_or_identical(service, content):
    with pytest.raises(ValidationError):
        service.propose(content, "x")
