# language: zh-TW
功能: 以業務需求產出 Java 專案的 Pull Request

  場景: 業務需求成功轉為 PR
    假設 目標 repo 已設定為 "https://github.com/example/outcome.git"
    並且 LLM 可用
    當 使用者輸入業務需求 "開發一個 backend 服務，可以新增、修改、刪除、查詢的個人資料服務"
    那麼 Agent 產出 spec.md、plan.md、tasks.md
    並且 每個 task 都先有失敗測試再有通過實作
    並且 Gradle 全部測試通過
    並且 每個階段的 commit 都已推送到目標 repo 的 feature 分支
    並且 目標 repo 出現一個標題含功能名稱、已轉為 Ready for review 的 PR
    並且 PR 描述包含所有 tasks 的 checklist

  場景: 測試未通過時 PR 維持 Draft
    假設 目標 repo 已設定為 "https://github.com/example/outcome.git"
    並且 產出的程式碼在 Gradle 測試失敗且重試後仍失敗
    當 使用者輸入業務需求 "開發一個 backend 服務，可以新增、修改、刪除、查詢的個人資料服務"
    那麼 PR 維持 Draft 狀態且不可審查合併
    並且 PR 描述與 Run 皆回報失敗階段與錯誤摘要
