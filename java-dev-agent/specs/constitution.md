# Constitution — Java Coding Agent

本文件定義「Java Coding Agent」專案自身的開發憲章。所有 spec、plan、tasks 與程式碼都必須遵守。

## 1. 工作方式：Spec-Driven Development (SDD)

每個功能依序產出並審核以下文件，缺一不可：

1. `specs/<id>-<slug>/spec.md` — 業務需求、使用者情境、驗收條件（Gherkin）。不描述實作。
2. `specs/<id>-<slug>/plan.md` — 技術設計：架構、模組邊界、介面、資料模型、風險。
3. `specs/<id>-<slug>/tasks.md` — 可獨立驗證的任務清單，每項對應測試。
4. 程式碼 — 每個 task 以 TDD 完成：先寫失敗測試，再寫最少實作，再重構。

## 2. Agent 自身的架構原則

- 六角形架構：`domain`（純模型與 ports）← `application`（use cases）← `adapters`（inbound: web；outbound: LLM、Langfuse、GitHub、Gradle、DB）。依賴只能朝內。
- 以 `import-linter` 在 CI 中強制依賴方向。
- SOLID：每個 use case 單一職責；所有外部系統透過 Protocol（port）注入；禁止在 domain/application 匿名 import 第三方 SDK。
- LLM、Langfuse、GitHub、檔案系統、子程序皆為 outbound adapter，可被 fake 取代。

## 3. 測試與評估

- 確定性測試（每次 CI 必跑）：pytest 單元測試、pytest-bdd 情境、import-linter。使用 FakeLlm，不呼叫真實模型。
- 整合測試（標記 `integration`）：Testcontainers PostgreSQL；需 Docker。
- 評估（標記 `live_llm`，獨立指令 `java-coding-agent-eval`）：以黃金資料集驅動完整 SDD 流程，量測產出 Java 專案的編譯、測試、ArchUnit、Cucumber 覆蓋率，並以 LLM-as-judge 評分；結果寫入 Langfuse Datasets / Scores；以門檻判定，不以精確比對。

## 4. 可觀測性

- 所有 LLM 呼叫經由 Langfuse CallbackHandler 追蹤；每次 SDD 執行為一個 trace，每個階段為一個 span。
- System Prompt 以 Langfuse Prompt Management 作為版本庫；Langfuse 不可用時退回本機檔案庫，功能不受影響。

## 5. 安全

- GitHub token、Langfuse key 只能來自環境變數或 `.env`，禁止進入 repo 或 log。
- 由對話修改 System Prompt 的動作必須明確確認且留下版本紀錄，可隨時還原預設。
- Agent 執行產出程式碼的 Gradle 測試時，一律在隔離的 workspace 目錄中進行。

## 6. 語言

- Agent 自身的文件與程式註解：繁體中文為主，識別字用英文。
- Agent 產出的 Java 程式：註解與 Cucumber feature 使用繁體中文，識別字用英文。
