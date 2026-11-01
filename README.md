# agents-generator-PoC

以 LangChain + LangGraph + Langfuse 建構、由本機 Ollama 模型驅動的 Agent 系列概念驗證。

## 內容

| 目錄 | 說明 |
|---|---|
| [`java-dev-agent/`](java-dev-agent/) | **Java Dev Agent**：把自然語言需求轉成 Java 17 / Spring Boot 4 / Gradle 六角形架構專案的 SDD（spec → plan → tasks → 每個任務 TDD RED/GREEN → verify → publish）開發代理，產出推送到目標 GitHub repo 並建立 PR。完整說明、架構與驗證紀錄見其 README。 |

## 狀態

- Java Dev Agent：已完成端對端驗證（2026-11-01）。以需求「開發一個 backend 服務，可以新增、修改、刪除、查詢的個人資料服務」驅動，產出 PR [ollama-agent-outcome#66](https://github.com/ChunPingWang/ollama-agent-outcome/pull/66)，fresh clone `./gradlew test` 53 個測試全數通過（含 ArchUnit、Cucumber zh-TW、Testcontainers）。
- 下一步：把這套「需求 → 骨架 → SDD/TDD 流程 → 確定性防呆 → 評估」的做法抽成可重用的 Agent Generator（待討論）。
