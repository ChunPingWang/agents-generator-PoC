import pytest

from java_coding_agent.application.settings_service import LlmSettingsService, RepoSettingsService
from java_coding_agent.domain.errors import ValidationError
from tests.unit.fakes import InMemorySettings


def test_update_llm_settings_persists_and_notifies():
    repo = InMemorySettings()
    seen = []
    svc = LlmSettingsService(repo, on_change=seen.append)
    s = svc.update(model="qwen3.6:35b-a3b-coding-mxfp8", context_length=32768, temperature=None)
    assert repo.llm.model == "qwen3.6:35b-a3b-coding-mxfp8"
    assert s.context_length == 32768
    assert seen == [s]


def test_invalid_llm_update_is_not_persisted():
    repo = InMemorySettings()
    with pytest.raises(ValidationError):
        LlmSettingsService(repo).update(temperature=5)
    assert repo.llm.temperature == 0.0


def test_update_repo_settings():
    repo = InMemorySettings()
    r = RepoSettingsService(repo).update(url="https://github.com/acme/svc.git", base_branch="develop")
    assert (r.full_name, r.base_branch) == ("acme/svc", "develop")
    assert repo.repo == r
