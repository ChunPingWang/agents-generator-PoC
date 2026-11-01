# Tasks 001 — Java Coding Agent

每項任務以 TDD 完成（先測試再實作）。「驗證」欄為對應測試。

## A. 專案骨架
- [x] A1 pyproject、uv 環境、import-linter 契約 — 驗證：`uv run lint-imports`
- [x] A2 憲章、spec、plan、tasks 文件

## B. Domain
- [x] B1 模型：`LlmSettings`、`RepoSettings`、`SystemPromptVersion`、`Message/Conversation`、`FeatureRun` 與階段狀態、`BuildResult`、`GeneratedFile`、`TaskItem` — 驗證：`tests/unit/domain`
- [x] B2 Ports（Protocol）：`LlmPort`、`PromptStorePort`、`SettingsRepository`、`ConversationRepository`、`RunRepository`、`CodeRepositoryPort`、`BuildRunnerPort`、`ScaffolderPort`
- [x] B3 `FileBlockParser`：解析 `=== FILE: path ===` 區塊 — 驗證：`test_file_block_parser.py`

## C. Application
- [x] C1 `SystemPromptService`：查看、提議變更、確認、還原 — 驗證：`test_system_prompt_service.py`
- [x] C2 `LlmSettingsService`、`RepoSettingsService` — 驗證：`test_settings_services.py`
- [x] C3 `SddPipeline`（LangGraph）：specify→plan→tasks→scaffold→implement→verify→publish；RED/GREEN 檢查；重試；失敗不開 PR — 驗證：`test_sdd_pipeline.py`（FakeLlm、FakeBuildRunner、FakeRepo）
- [x] C4 `RunRegistry`：背景執行與事件串流 — 驗證：`test_run_registry.py`
- [x] C5 `ChatService`：tool-calling agent 與 tools — 驗證：`test_chat_tools.py`

## D. Adapters
- [x] D1 `FilePromptStore`、`LangfusePromptStore` — 驗證：單元（檔案）、整合（Langfuse 可選）
- [x] D2 SQLAlchemy repositories — 驗證：單元（SQLite）、整合（Testcontainers PostgreSQL）
- [x] D3 LangChain model 工廠（Ollama/OpenAI/Anthropic）+ Langfuse callback — 驗證：`test_model_factory.py`
- [x] D4 `GradleBuildRunner` — 驗證：`test_gradle_runner.py`（假 gradlew）
- [x] D5 `GitPythonGitHubRepository` — 驗證：本機 bare repo 單元測試；PR 以 fake client
- [x] D6 `JinjaScaffolder` 與 Java 模板 — 驗證：模板渲染測試 + 實際 `gradle test` 一次
- [x] D7 FastAPI：`/api/chat`（SSE）、`/api/runs/{id}/events`、設定 API、靜態頁 — 驗證：`test_web_api.py`

## E. BDD
- [x] E1 pytest-bdd 情境：對應 spec.md 四個場景 — 驗證：`tests/bdd`

## F. 評估
- [x] F1 黃金資料集 `evals/dataset/cases.jsonl`
- [x] F2 評估 runner：編譯/測試/ArchUnit/Cucumber 指標 + LLM-as-judge；寫入 Langfuse Datasets/Scores；門檻判定

## G. 維運
- [x] G1 docker-compose：Langfuse + PostgreSQL
- [x] G2 README：啟動、設定、評估方式

## H. 端對端驗證（使用者指定的業務需求）
- [x] H1 以業務需求「開發一個 backend 服務，可以新增、修改、刪除、查詢的個人資料服務」驅動 Agent 完整執行，使用本機 Ollama Qwen
- [x] H2 確認目標 repo 出現 PR，產出專案 `./gradlew test` 全數通過（含 ArchUnit、Cucumber、Testcontainers）
- [x] H3 Agent 自身 pytest 單元測試、pytest-bdd、import-linter 全數通過
- [x] H4 README.md 完整說明
