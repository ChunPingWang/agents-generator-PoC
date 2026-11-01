# Spec 001 — Java Coding Agent

## 背景

使用者希望以自然語言描述業務邏輯，由 Agent 依 Spec-Driven Development 流程產出符合 SOLID、六角形架構、TDD/BDD 的 Java 17 + Spring Boot 4 專案，並以 Pull Request 交付至指定 GitHub repo。

## 名詞

- **業務需求**：使用者在對話框輸入的一段自然語言。
- **System Prompt**：Agent 產生程式碼時遵守的規則；出廠預設內含 Java 技術規則；可由對話修改、可還原。
- **LLM 設定**：provider、base URL、model、temperature、context length。
- **目標 Repo**：Agent 推送產出程式碼並開 PR 的 GitHub repo。
- **SDD 執行（Run）**：一次從業務需求到 PR 的完整流程。

## 使用者情境

### US-1 透過對話框輸入業務需求並取得 PR
身為使用者，我在對話框輸入業務需求後，Agent 應依序產出 spec、plan、tasks，以 TDD 實作並通過全部測試，最後在目標 repo 開一個 PR，PR 描述含 tasks checklist。每個主要階段的 commit 都要推送到 GitHub，tasks 拆解完成即建立 Draft PR 讓進度可見；全部驗證通過才轉為 Ready for review，失敗則維持 Draft 並寫入失敗摘要。

### US-2 以對話修改 System Prompt
身為使用者，我能以自然語言要求查看、修改或還原 System Prompt。修改前 Agent 需摘要變更並要求確認；每次修改建立新版本。

### US-3 以對話修改 LLM 設定
身為使用者，我能查看與修改 provider、base URL、model、temperature、context length；開發時預設為本機 Ollama 的 Qwen。

### US-4 設定目標 GitHub Repo
身為使用者，我能設定目標 repo URL 與 base branch；預設為 `https://github.com/ChunPingWang/ollama-agent-outcome.git`。

### US-5 觀測與評估
身為維運者，我能在 Langfuse 看到每次 Run 的 trace、每個階段的 span、prompt 版本與評估分數。

## 驗收條件（Gherkin）

```gherkin
功能: 以業務需求產出 Java 專案的 Pull Request

  場景: 業務需求成功轉為 PR
    假設 目標 repo 已設定為 "https://github.com/example/outcome.git"
    並且 LLM 可用
    當 使用者輸入業務需求 "會員下單時，若訂單金額超過 1000 元則給予 5% 折扣"
    那麼 Agent 產出 spec.md、plan.md、tasks.md
    並且 每個 task 都先有失敗測試再有通過實作
    並且 Gradle 全部測試通過
    並且 目標 repo 出現一個標題含功能名稱的 PR
    並且 PR 描述包含所有 tasks 的 checklist

  場景: 測試未通過時 PR 維持 Draft
    假設 產出的程式碼在 Gradle 測試失敗且重試後仍失敗
    那麼 PR 維持 Draft 狀態且不可審查合併
    並且 PR 描述與 Run 皆回報失敗階段與錯誤摘要

功能: 以對話管理 System Prompt

  場景: 修改 System Prompt
    當 使用者說 "請把規則改成所有 REST API 都要有 OpenAPI 註解"
    那麼 Agent 回覆變更摘要並詢問確認
    當 使用者確認
    那麼 System Prompt 產生新版本且內容包含 "OpenAPI"

  場景: 還原 System Prompt
    當 使用者說 "還原預設的 System Prompt"
    那麼 System Prompt 內容等於出廠預設

功能: 以對話管理 LLM 設定

  場景: 修改模型
    當 使用者說 "模型改用 qwen3.6:35b-a3b-coding-mxfp8，context 設為 32768"
    那麼 LLM 設定的 model 為 "qwen3.6:35b-a3b-coding-mxfp8"
    並且 context length 為 32768
```

## 非功能需求

- 對話回應需串流輸出；SDD Run 在背景執行並以事件串流回報進度。
- 本機 Ollama 單次 Run 可能需數分鐘；UI 須可持續顯示進度。
- Langfuse 不可用時，Agent 核心功能不中斷。

## 範圍外

- 多使用者權限、登入。
- 部署至雲端。
