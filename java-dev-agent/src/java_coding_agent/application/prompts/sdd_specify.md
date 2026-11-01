你正在執行 Spec-Driven Development 的第一階段：撰寫 **spec.md**。

業務需求：
<requirement>
{{ requirement }}
</requirement>

請依下列格式輸出 **一個** 檔案區塊 `=== FILE: specs/{{ feature_id }}-{{ feature_slug }}/spec.md ===`，內容為 Markdown，包含：

1. `# Spec {{ feature_id }} — <功能名稱>`
2. `## 背景`：一段話說明要解決的問題。
3. `## 名詞`：領域名詞與定義。
4. `## 使用者情境`：每個情境 `### US-n <標題>`，以「身為…我想要…以便…」描述。
5. `## 驗收條件`：以 Gherkin（繁體中文關鍵字：功能、場景、假設、當、那麼、並且）撰寫，置於 ```gherkin 區塊，每個情境至少一個場景，包含錯誤情境（如查無資料、重複、欄位不合法）。
6. `## API 概要`：一張表列出 HTTP 方法、路徑、成功/失敗狀態碼。
7. `## 範圍外`。

只描述「做什麼」，不描述實作細節（不提類別名稱、資料庫表）。
在檔案區塊之後，另輸出一行 `FEATURE_NAME: <英文短名，小寫以連字號分隔，例如 personal-profile-crud>`。
