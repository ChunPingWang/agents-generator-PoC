# Plan 001 — Java Coding Agent

## 技術選型

| 面向 | 選擇 | 理由 |
|---|---|---|
| 語言 | Python 3.13 | LangChain / Langfuse 官方 SDK 皆為 Python |
| Agent 框架 | LangChain 1.x `create_agent` + LangGraph | 對話層用 tool-calling agent；SDD 流程用 LangGraph 狀態圖，步驟可控、可重試 |
| LLM | `langchain-ollama`（預設）、`langchain-openai`、`langchain-anthropic` | provider 可切換，以 port 隔離 |
| 可觀測性 | Langfuse Python SDK v5：`CallbackHandler`、Prompt Management、Datasets/Scores | 一套工具同時解決 tracing、prompt 版本、評估 |
| Web | FastAPI + SSE + 單頁 HTML/JS | 易測試（httpx TestClient），串流簡單 |
| 持久化 | SQLAlchemy 2 + PostgreSQL（dev/prod）、SQLite（單元測試） | 設定、對話、Run 紀錄 |
| Git / GitHub | GitPython + PyGithub | 建分支、commit、push、開 PR |
| Java 建置 | subprocess 執行 `./gradlew` | 在隔離 workspace 驗證產出程式碼 |
| 產出專案骨架 | Jinja2 模板 | Gradle 9.8 多模組、Spring Boot 4.1.2、JUnit Jupiter 6、Cucumber 7.32（7.33+ 對 zh-TW 有 locale bug）、Testcontainers 2.0、ArchUnit 1.5 由模板確定性產生並已實際建置驗證；LLM 只負責 domain/application/adapter 程式碼 |

## 六角形架構（Agent 自身）

```
java_coding_agent/
  domain/            純 Python：模型、值物件、ports（Protocol）、領域例外
  application/       use cases：ChatService、SystemPromptService、LlmSettingsService、
                     RepoSettingsService、SddPipeline（LangGraph）、RunRegistry
  adapters/
    inbound/web/     FastAPI routers、SSE、靜態聊天頁
    outbound/llm/    LangChain chat model 工廠、Langfuse callback、LangChainLlm（實作 LlmPort）
    outbound/prompt_store/  LangfusePromptStore、FilePromptStore
    outbound/persistence/   SQLAlchemy 實作 SettingsRepository、ConversationRepository、RunRepository
    outbound/github/        GitPythonGitHubRepository（實作 CodeRepositoryPort）
    outbound/build/         GradleBuildRunner（實作 BuildRunnerPort）
    outbound/scaffold/      JinjaScaffolder（實作 ScaffolderPort）
  config.py          pydantic-settings
  main.py            組裝（composition root）
  evaluation/        評估 runner（屬 inbound adapter 性質的 CLI）
```

### Ports

- `LlmPort.complete(messages, *, trace) -> str`、`stream(...)`、`structured(messages, schema)`。
- `PromptStorePort.get_active() / save_new_version(content, note) / reset_to_default() / history()`。
- `SettingsRepository`（LLM 設定、Repo 設定）、`ConversationRepository`、`RunRepository`。
- `CodeRepositoryPort.prepare_workspace(repo, base) / write_files / commit / push_branch / open_pull_request`。
- `BuildRunnerPort.run_tests(workspace, *, task_filter) -> BuildResult`。
- `ScaffolderPort.scaffold(workspace, project) -> list[Path]`。
- `ClockPort`、`IdGenerator`（可測性）。

## 對話層設計

`ChatService` 以 `create_agent` 建立 tool-calling agent，tools：
- `show_system_prompt`、`propose_system_prompt_change(new_content, summary)`、`confirm_system_prompt_change(change_id)`、`reset_system_prompt`
- `show_llm_settings`、`update_llm_settings(...)`
- `show_repo_settings`、`update_repo_settings(url, base_branch)`
- `start_feature_run(business_requirement)` → 建立背景 Run，回傳 run_id

System Prompt 分兩段組合：**對話層 agent 指示**（固定，描述工具使用方式）+ **Java 規則 System Prompt**（可編輯、由 PromptStore 取得）。產碼時只用後者加上 SDD 階段模板。

## SDD Pipeline（LangGraph）

```
prepare → specify → plan → tasks → implement(loop per task) → verify → publish
  │                                     │
  │                                     ├─ write_test → run(expect RED) → write_impl → run(expect GREEN)
  │                                     └─ 失敗則帶著錯誤重試，最多 N 次；超過則 Run 失敗，不開 PR
  └─ clone 目標 repo、建立分支；repo 無骨架時以模板產生並先 `gradlew test` 驗證骨架
```

> 實作註記：workspace 準備（含骨架）必須先於 specify，因為 feature id 由 repo 內既有 `specs/` 數量決定，
> 且 LLM 需要知道基底套件名稱。事件階段名稱沿用 `scaffold`。

狀態：`requirement, feature_slug, spec_md, plan_md, tasks, current_task_index, files, build_results, pr_url, error`。
每個節點以 Langfuse span 包覆；LLM 輸出的檔案以 `=== FILE: path ===` 區塊格式解析（對小模型較穩定），並有單元測試。

## 產出 Java 專案的結構（模板）

```
settings.gradle.kts, build.gradle.kts(root), gradle/wrapper/*
<app>-domain/        純 Java，無 Spring
<app>-application/   use cases、ports
<app>-adapter-in-web/   Spring MVC
<app>-adapter-out-persistence/  Spring Data JPA + Testcontainers PostgreSQL
<app>-bootstrap/     Spring Boot 4 應用程式、ArchUnit 架構測試、Cucumber runner
specs/<id>-<slug>/{spec.md, plan.md, tasks.md}
```

ArchUnit 規則：domain 不依賴 Spring/JPA；application 不依賴 adapters；adapters 不互相依賴。

## 風險與對策

| 風險 | 對策 |
|---|---|
| 本機 Qwen 輸出格式不穩 | 檔案區塊格式 + 解析失敗自動重試；temperature 0、固定 seed |
| 單次 Run 時間長 | 背景執行 + SSE 進度；UI 顯示每階段狀態 |
| Spring Boot 4 / JUnit 6 / Cucumber 8 相依衝突 | 骨架模板先以 Gradle 實際建置驗證，鎖版本 |
| 目標 repo 為空 | publish 前若無 base branch，先建立初始 commit |
| Langfuse 不可用 | PromptStore 退回檔案；tracing 以 no-op 處理 |
