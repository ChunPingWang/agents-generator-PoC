# language: zh-TW
功能: 以對話管理 System Prompt

  場景: 修改 System Prompt
    當 使用者說 "請把規則改成所有 REST API 都要有 OpenAPI 註解"
    那麼 Agent 回覆變更摘要並詢問確認
    當 使用者確認
    那麼 System Prompt 產生新版本且內容包含 "OpenAPI"

  場景: 還原 System Prompt
    假設 System Prompt 已被修改過
    當 使用者說 "還原預設的 System Prompt"
    那麼 System Prompt 內容等於出廠預設
