"""Composition root：組裝 adapters 與 application，啟動 FastAPI。"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from java_coding_agent.adapters.inbound.web.app import create_app
from java_coding_agent.adapters.outbound.build.gradle_runner import GradleBuildRunner
from java_coding_agent.adapters.outbound.github.git_github_repository import GitGitHubRepository
from java_coding_agent.adapters.outbound.llm.langchain_llm import LangChainLlm
from java_coding_agent.adapters.outbound.llm.langfuse_tracing import LangfuseTracing
from java_coding_agent.adapters.outbound.llm.model_factory import LangChainModelFactory
from java_coding_agent.adapters.outbound.persistence.repositories import (
    SqlConversationRepository,
    SqlRunRepository,
    SqlSettingsRepository,
    make_engine,
)
from java_coding_agent.adapters.outbound.prompt_store.file_prompt_store import FilePromptStore
from java_coding_agent.adapters.outbound.prompt_store.langfuse_prompt_store import LangfusePromptStore
from java_coding_agent.adapters.outbound.scaffold.jinja_scaffolder import JinjaScaffolder
from java_coding_agent.application.chat_service import ChatService
from java_coding_agent.application.run_registry import RunRegistry
from java_coding_agent.application.sdd_pipeline import PipelineOptions, SddPipeline
from java_coding_agent.application.settings_service import LlmSettingsService, RepoSettingsService
from java_coding_agent.application.system_prompt_service import SystemPromptService
from java_coding_agent.application.templates import PromptTemplates
from java_coding_agent.config import AppConfig
from java_coding_agent.domain.models import LlmSettings, RepoSettings
from java_coding_agent.domain.ports import PromptStorePort

log = logging.getLogger(__name__)


@dataclass
class Container:
    config: AppConfig
    chat: ChatService
    runs: RunRegistry
    system_prompt: SystemPromptService
    llm_settings: LlmSettingsService
    repo_settings: RepoSettingsService
    tracing: LangfuseTracing


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def build_container(config: AppConfig | None = None) -> Container:
    config = config or AppConfig()
    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    templates = PromptTemplates()
    engine = make_engine(config.effective_database_url)
    settings_repo = SqlSettingsRepository(
        engine,
        default_llm=LlmSettings(
            provider=config.llm_provider,  # type: ignore[arg-type]
            base_url=config.llm_base_url,
            model=config.llm_model,
            temperature=config.llm_temperature,
            context_length=config.llm_context_length,
        ).updated(provider=config.llm_provider),
        default_repo=RepoSettings(url=config.default_repo_url, base_branch=config.default_repo_base_branch),
    )

    tracing = LangfuseTracing(
        public_key=config.langfuse_public_key, secret_key=config.langfuse_secret_key, host=config.langfuse_host
    )
    default_prompt = templates.default_system_prompt()
    prompt_store: PromptStorePort
    if config.prompt_store == "langfuse" or (config.prompt_store == "auto" and tracing.enabled):
        try:
            prompt_store = LangfusePromptStore(tracing.client, default_prompt)
            log.info("System Prompt 版本庫：Langfuse Prompt Management")
        except Exception as exc:
            log.warning("Langfuse Prompt Management 不可用（%s），改用檔案", exc)
            prompt_store = FilePromptStore(config.data_dir / "system_prompt.json", default_prompt)
    else:
        prompt_store = FilePromptStore(config.data_dir / "system_prompt.json", default_prompt)
        log.info("System Prompt 版本庫：檔案 %s", config.data_dir / "system_prompt.json")

    model_factory = LangChainModelFactory(
        request_timeout_seconds=config.llm_request_timeout_seconds,
        max_output_tokens=config.llm_max_output_tokens,
        thinking=config.llm_thinking,
    )
    llm = LangChainLlm(model_factory, settings_repo, tracing, call_timeout_seconds=config.llm_request_timeout_seconds)
    builder = GradleBuildRunner(
        java_home=config.java_home,
        gradle_bin=config.gradle_bin,
        gradle_version=config.gradle_version,
        timeout_seconds=config.gradle_timeout_seconds,
    )
    scaffolder = JinjaScaffolder()
    options = PipelineOptions(
        max_fix_attempts=config.pipeline_max_fix_attempts,
        max_parse_retries=config.pipeline_max_parse_retries,
        strict_red=config.pipeline_strict_red,
    )

    def pipeline_factory(on_event) -> SddPipeline:
        return SddPipeline(
            llm=llm,
            code_repo=GitGitHubRepository(token=config.github_token),
            builder=builder,
            scaffolder=scaffolder,
            prompt_store=prompt_store,
            settings=settings_repo,
            templates=templates,
            workspace_root=config.workspace_root,
            options=options,
            on_event=on_event,
        )

    run_repository = SqlRunRepository(engine)
    stale = run_repository.fail_stale_runs("伺服器重新啟動，執行中斷")
    if stale:
        log.warning("已將 %d 個未完成的 Run 標記為失敗", stale)
    runs = RunRegistry(
        pipeline_factory=pipeline_factory,
        run_repository=run_repository,
        id_generator=new_id,
        tracing=tracing,
    )
    system_prompt = SystemPromptService(prompt_store, new_id)
    llm_settings = LlmSettingsService(settings_repo)
    repo_settings = RepoSettingsService(settings_repo)
    chat = ChatService(
        model_factory=model_factory,
        llm_settings=llm_settings,
        repo_settings=repo_settings,
        system_prompt=system_prompt,
        runs=runs,
        conversations=SqlConversationRepository(engine),
        templates=templates,
        callbacks_provider=lambda: tracing.callbacks(trace_name="chat"),
    )
    return Container(config, chat, runs, system_prompt, llm_settings, repo_settings, tracing)


def run() -> None:
    import uvicorn

    container = build_container()
    app = create_app(container)
    uvicorn.run(
        app, host=container.config.host, port=container.config.port, log_level=container.config.log_level.lower()
    )


if __name__ == "__main__":
    run()
