from pathlib import Path

import pytest

from java_coding_agent.application.sdd_pipeline import PipelineOptions, SddPipeline
from java_coding_agent.application.templates import PromptTemplates
from tests.unit.fakes import (
    FakeBuildRunner,
    FakeCodeRepository,
    FakeScaffolder,
    InMemoryPromptStore,
    InMemorySettings,
    scripted_llm,
)


@pytest.fixture
def templates() -> PromptTemplates:
    return PromptTemplates()


@pytest.fixture
def prompt_store(templates) -> InMemoryPromptStore:
    return InMemoryPromptStore(templates.default_system_prompt())


@pytest.fixture
def make_pipeline(templates, prompt_store, tmp_path: Path):
    """回傳建構器：可覆寫 llm / builder / code_repo / options。"""

    def _make(llm=None, builder=None, code_repo=None, options=None, events=None):
        return SddPipeline(
            llm=llm or scripted_llm(),
            code_repo=code_repo or FakeCodeRepository(),
            builder=builder or FakeBuildRunner([True, False, True]),
            scaffolder=FakeScaffolder(),
            prompt_store=prompt_store,
            settings=InMemorySettings(),
            templates=templates,
            workspace_root=tmp_path,
            options=options or PipelineOptions(),
            on_event=(events.append if events is not None else None),
        )

    return _make
