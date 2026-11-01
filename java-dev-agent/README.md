# Java Coding Agent

以自然語言輸入業務需求，由 Agent 依 **Spec-Driven Development（SDD）** 流程產出符合 **SOLID、六角形架構、TDD/BDD** 的 **Java 17 + Spring Boot 4** 專案，並以 **Pull Request** 交付到指定的 GitHub repo。

- Agent 本身：**Python 3.13 + LangChain 1.x / LangGraph + Langfuse**（可觀測性、Prompt 版本管理、評估）。
- Agent 的輸入：可編輯的 System Prompt（內含 Java 技術規則）、業務需求、LLM 與 GitHub 設定。
- Agent 的輸出：Gradle 多模組 Java 專案（domain / application / adapter-web / adapter-persistence / bootstrap）、spec/plan/tasks 文件、通過 `./gradlew test`（JUnit 6、Cucumber、Testcontainers、ArchUnit）的程式碼，以及 GitHub PR。

> 端對端驗證結果見本文末「驗證紀錄」。

---

## 1. 功能總覽

| 需求 | 實作 |
|---|---|
| 對話框輸入業務邏輯 | Web 聊天介面（FastAPI + SSE 串流）；LangChain tool-calling agent 判斷「管理」或「開發」意圖 |
| 以對話修改 System Prompt | 工具 `propose_system_prompt_change` → 使用者確認 → `confirm_system_prompt_change`；可 `reset_system_prompt` 還原預設；每次修改建立新版本（Langfuse Prompt Management 或本機檔案） |
| 以對話修改 LLM 設定 | 工具 `update_llm_settings`：provider（ollama / openai / anthropic）、base_url、model、temperature、context_length；開發預設本機 Ollama `qwen3.6:35b-a3b-coding-mxfp8` |
| 產出寫入可設定的 GitHub repo | 工具 `update_repo_settings`；預設 `https://github.com/ChunPingWang/ollama-agent-outcome.git`；空 repo 會自動建立 base branch |
| SDD | `prepare → specify → plan → tasks → implement(每個 task RED→GREEN) → verify → publish`，文件寫入目標 repo 的 `specs/<id>-<slug>/` |
| TDD | 每個 task 先寫測試並執行 Gradle 確認失敗（RED），再寫實作確認通過（GREEN）；失敗自動修正最多 N 次；仍失敗則不開 PR |
| BDD | spec 的驗收條件為繁體中文 Gherkin；產出專案以 Cucumber 7 + `# language: zh-TW` 執行，透過 `TestRestTemplate` 打真實 API |
| 六角形架構 / SOLID | 骨架模板固定模組邊界；ArchUnit 測試強制依賴方向；System Prompt 要求 port/adapter、建構子注入、介面分離 |
| Testcontainers | 產出專案的 persistence 測試與 Cucumber 測試使用 PostgreSQL 容器（`@ServiceConnection`） |
| task 寫入 PR | PR 描述含 `- [x] Tn` checklist，每個 task 一個 commit |
| 可觀測性 | Langfuse：對話與每次 SDD Run 的 trace / span、prompt 版本連結、評估分數 |
| Agent 測試與評估 | pytest 單元測試（fakes）、pytest-bdd 情境、import-linter 架構契約、Testcontainers 整合測試；`java-coding-agent-eval` 以黃金資料集跑真實流程並計算指標 + LLM-as-judge |

## 2. 架構（Agent 自身也採六角形架構）

```
src/java_coding_agent/
  domain/        模型（LlmSettings, RepoSettings, FeatureRun, TaskItem, BuildResult…）、ports（Protocol）、
                 檔案區塊解析器、tasks.md 解析器。不依賴任何框架。
  application/   ChatService（LangChain create_agent + tools）、SddPipeline（LangGraph 狀態圖）、
                 RunRegistry（背景執行 + 事件串流）、SystemPromptService、Settings services、prompt 模板
  adapters/
    inbound/web/            FastAPI：/api/chat(SSE)、/api/runs/{id}/events(SSE)、設定與 System Prompt API、聊天頁
    outbound/llm/           LangChain model 工廠（Ollama/OpenAI/Anthropic）、LlmPort 實作、Langfuse tracing
    outbound/prompt_store/  LangfusePromptStore、FilePromptStore
    outbound/persistence/   SQLAlchemy（SQLite 或 PostgreSQL）
    outbound/github/        GitPython + PyGithub（clone、分支、commit、push、PR）
    outbound/build/         GradleBuildRunner（隔離 workspace 執行 ./gradlew test）
    outbound/scaffold/      Jinja2 模板產生 Java 專案骨架
  evaluation/    評估 runner 與指標
  config.py / main.py       pydantic-settings、composition root
specs/           Agent 自身的 SDD 文件（constitution、spec、plan、tasks）
tests/           unit / bdd / integration
evals/           黃金資料集與評估結果
```

依賴方向由 `import-linter` 契約強制（`uv run lint-imports`）：domain 不依賴任何內部模組；application 不依賴 adapters；inbound 不依賴 outbound。

### SDD Pipeline（LangGraph）

```
prepare   clone 目標 repo、建立 feature 分支；無骨架則由模板產生並先跑 ./gradlew test 驗證骨架
specify   LLM 產出 specs/<id>-<slug>/spec.md（含繁體中文 Gherkin 驗收條件）
plan      LLM 產出 plan.md（領域模型、ports、adapters、測試策略、類別清單）
tasks     LLM 產出 tasks.md（4–8 個可獨立 TDD 的任務），系統解析為 TaskItem
implement 每個 task：寫測試 → gradlew test（預期 RED）→ 寫實作 → gradlew test（預期 GREEN）→ 失敗則帶錯誤修正 → commit
verify    完整 ./gradlew test（含 ArchUnit、Cucumber、Testcontainers）；更新 tasks.md 勾選
publish   push 分支、LLM 撰寫 PR 描述（含 tasks checklist）、開 PR
```

LLM 的多檔案輸出一律使用 `=== FILE: path ===` … `=== END FILE ===` 區塊格式（對本機小模型最穩定），解析失敗會把錯誤回饋給模型重試。

### 產出的 Java 專案結構

```
settings.gradle.kts / build.gradle.kts / gradle.properties   Gradle 9.8、JDK 17 toolchain、Spring Boot 4.1.2 BOM
domain/               純 Java；ArchUnit 禁止依賴 Spring / JPA / Jackson / Lombok
application/          use cases、port.in / port.out；禁止 Spring
adapter-web/          Spring MVC、Jakarta Validation；@WebMvcTest
adapter-persistence/  Spring Data JPA、PostgreSQL；@DataJpaTest + Testcontainers
bootstrap/            @SpringBootApplication、UseCaseConfiguration、ArchitectureTest（ArchUnit）、
                      CucumberTest（JUnit Platform Suite）、CucumberSpringConfiguration、features/*.feature
specs/<id>-<slug>/    spec.md、plan.md、tasks.md
```

版本說明：Cucumber 鎖定 **7.32.0**。7.33 以後與 8.0.x 的 `Runner.localeForPickle` 改用 `Locale.Builder`，對 `zh-TW` 等含地區碼的語言會拋 `IllformedLocaleException`（cucumber-jvm PR #3117）。Spring Boot 4 的測試註解套件與 Boot 3 不同（例如 `org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest`、`org.springframework.boot.resttestclient.TestRestTemplate` 需搭配 `@AutoConfigureTestRestTemplate`），這些已寫入預設 System Prompt 的速查表。

## 3. 安裝與啟動

### 需求
- macOS / Linux、Docker（Testcontainers 與 Langfuse）
- Python 3.13（建議以 [uv](https://docs.astral.sh/uv/) 管理）
- JDK 17 與 Gradle（用來產生 wrapper）：`brew install openjdk@17 gradle`
- Ollama 與模型：`ollama pull qwen3.6:35b-a3b-coding-mxfp8`
- GitHub 權杖：`GITHUB_TOKEN`，或已 `gh auth login`

### 步驤
```bash
uv sync --extra dev
cp .env.example .env            # 視需要修改 JAVA_HOME、GITHUB_TOKEN、Langfuse 金鑰
docker compose up -d            # Langfuse（http://localhost:3000，帳號 dev@example.com / devpassword1）+ PostgreSQL
uv run java-coding-agent        # http://127.0.0.1:8000
```

在聊天框輸入，例如：
- `顯示目前的 System Prompt`
- `請把規則改成所有 REST API 都要有 OpenAPI 註解` → Agent 摘要變更並詢問 → 回「確認」
- `模型改用 qwen3.6:35b-a3b-coding-mxfp8，context 設為 32768`
- `目標 repo 改成 https://github.com/<owner>/<repo>.git`
- `開發一個 backend 服務，可以新增、修改、刪除、查詢的個人資料服務` → 啟動 SDD Run，右側面板顯示各階段進度與 PR 連結

也可直接呼叫 API：`POST /api/runs {"requirement": "..."}`、`GET /api/runs/{id}/events`（SSE）。

### 主要環境變數（`.env.example`）
| 變數 | 說明 |
|---|---|
| `LLM_PROVIDER` / `LLM_BASE_URL` / `LLM_MODEL` / `LLM_CONTEXT_LENGTH` | LLM 預設值（執行期可由對話修改並持久化） |
| `DEFAULT_REPO_URL` / `DEFAULT_REPO_BASE_BRANCH` / `GITHUB_TOKEN` | 目標 repo 與權杖 |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` / `PROMPT_STORE` | 未設定金鑰時 tracing 為 no-op、System Prompt 存檔案 |
| `DATABASE_URL` | 空值用 SQLite；可指向 compose 的 PostgreSQL |
| `JAVA_HOME` / `GRADLE_BIN` / `GRADLE_TIMEOUT_SECONDS` | 執行產出專案測試用 |
| `PIPELINE_MAX_FIX_ATTEMPTS` / `PIPELINE_STRICT_RED` | 修正次數上限；RED 階段若測試意外通過是否視為失敗 |

## 4. Agent 的測試與評估

```bash
uv run pytest                      # 單元 + BDD（fakes，不需 LLM/Docker）；目前 215 個測試
uv run pytest -m integration       # Testcontainers PostgreSQL 整合測試（需 Docker）
uv run lint-imports                # 六角形依賴契約
uv run ruff check src tests && uv run mypy
uv run java-coding-agent-eval      # 評估：真實 LLM + Gradle + GitHub，結果寫入 evals/results 與 Langfuse
```

| 層級 | 內容 |
|---|---|
| 單元測試 | domain 模型與解析器、SystemPromptService、Settings services、SddPipeline（成功 / 修正耗盡 / strict RED / 骨架失敗 / 解析重試 / push 失敗 / PR 描述失敗退回）、RunRegistry 事件串流、ChatService 工具路由、各 adapter（檔案 prompt store、SQLite repositories、Gradle runner 假 gradlew、Git 本機 bare repo、骨架渲染、model 工廠）、FastAPI 端點 |
| BDD | `tests/bdd/features/*.feature`（zh-TW Gherkin）對應 spec.md 的場景，以 pytest-bdd 執行 |
| 整合 | PostgreSQL（Testcontainers）repositories |
| 評估 | `evals/dataset/cases.jsonl` 黃金資料集；指標：成功率、TDD 紀律（RED 確認比例）、修正輪數、Gherkin 場景數、需求關鍵字覆蓋；LLM-as-judge（需求覆蓋、六角形設計、可測試性、清晰度 1–5 分）；門檻：成功率 ≥ 2/3、TDD 紀律 ≥ 0.5、judge 平均 ≥ 3 |

## 5. 可觀測性（Langfuse）

- 每次對話與每次 SDD Run 以 `CallbackHandler` 送到 Langfuse；Run 的 trace 名稱為 `sdd-run-<id>`，LangGraph 每個節點與每次 LLM 呼叫為子 span（`sdd.specify`、`sdd.write_test`…）。
- System Prompt 以 Langfuse Prompt Management 的 `java-coding-rules` prompt 管理，每次修改為新版本並標 `production`；Langfuse 不可用時自動退回 `workspace/data/system_prompt.json`。
- 評估分數以 Langfuse Scores / Datasets（`java-coding-agent-golden`）記錄。

## 6. 專案文件（SDD）
- `specs/constitution.md`：開發憲章
- `specs/001-java-coding-agent/spec.md`、`plan.md`、`tasks.md`

## 7. 驗證紀錄

端對端驗證以業務需求「開發一個 backend 服務，可以新增、修改、刪除、查詢的個人資料服務」驅動，LLM 為本機 Ollama `qwen3.6:35b-a3b-coding-mxfp8`，目標 repo 為 `ChunPingWang/ollama-agent-outcome`。

### 7.1 最終成功的執行（2026-11-01，第 76 次嘗試）

| 項目 | 結果 |
|---|---|
| PR | https://github.com/ChunPingWang/ollama-agent-outcome/pull/66（Draft → Ready for review） |
| 任務數 | 8（Domain → Application ×2 → Persistence ×2 → Bootstrap → Web → Cucumber） |
| 總時間 | 15 分 35 秒（09:05:21 → 09:20:56 UTC） |
| RED 確認 | 5 / 8（2 個任務的測試在 RED 輸出即附帶實作而直接通過；bootstrap 組裝任務依規則略過 RED） |
| GREEN 修正輪數 | 13（最多單一任務 8 輪，含 1 次高推理重寫） |
| 獨立驗證 | fresh clone + `./gradlew test --no-build-cache --rerun-tasks`：53 個測試全數通過 |

獨立驗證的測試組成：domain 15、application 8、adapter-web 7、adapter-persistence 8（`@DataJpaTest` + Testcontainers PostgreSQL）、bootstrap 15（ApplicationContextTest 1、ArchUnit 4、Cucumber zh-TW 9 個場景 + 煙霧 1）。

### 7.2 歷程（誠實紀錄）

- 共 76 次端對端嘗試才得到可獨立驗證的 PR。第 56 次（PR #47）曾被標記 Ready，但 fresh clone 無法編譯：`.gitignore` 的 `out/` 把 `application/.../port/out/` 整個目錄排除；之後改為 `/out/` 並在每次 commit 強制加入 `src/**`。PR #47 保留原狀作為對照。
- 失敗嘗試留下的 Draft PR（#26–#65）未關閉，可從每個 PR 的 commit 看到該次執行走到哪個任務。
- 每次失敗都先在複本 workspace 以 `./gradlew test --offline` 重現真正的 javac / JUnit / Cucumber 錯誤，再把「本機模型反覆犯、提示詞修不好」的錯誤轉成**確定性的**正規化或驗證規則（`domain/java_paths.py`，目前 30 個正規化器、10 個驗證器），並以單元測試固定下來。典型例子：Cucumber 步驟方法非 public（所有步驟 undefined）、`@InjectMocks` 欄位初始化在 mock 注入前執行、Save Port 直接 `save(toEntity(...))` 造成更新撞主鍵回 409、前置步驟用 POST 建資料導致固定 UUID 不存在、`.feature` 在一般 `場景:` 底下放 `例子:` 表格、多個場景共用同一個 email 撞唯一約束。
- 其他基礎設施問題：Testcontainers 下 bootstrap 測試 JVM 曾卡住 30 分鐘（骨架現在對每個 Test task 設 10 分鐘 timeout）；Ollama mlx runner 偶爾在 prompt 處理後不輸出（LLM 呼叫有 900 秒看門狗）。

### 7.3 Agent 自身的檢查（同日）

| 檢查 | 結果 |
|---|---|
| `uv run pytest` | 215 passed（含 pytest-bdd 5 個場景；integration / live_llm 預設排除） |
| `uv run lint-imports` | 3 contracts kept, 0 broken |
| `uv run ruff check src tests` | All checks passed |
| `uv run mypy src` | Success: no issues found in 46 source files |
