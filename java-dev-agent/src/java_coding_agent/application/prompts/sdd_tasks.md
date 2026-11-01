你正在執行 Spec-Driven Development 的第三階段：拆解 **tasks.md**。

plan.md：
<plan>
{{ plan_md }}
</plan>

請把實作拆成 **4 到 8 個** 可獨立以 TDD 完成的任務。每個任務都必須有可執行的測試：純介面、Command/DTO 不可單獨成為任務，請併入使用它們的 use case 任務，順序必須為：domain → application（port + use case）→ adapter-persistence → **bootstrap 組裝（只需在骨架的 `UseCaseConfiguration` 以 `@Bean` 宣告所有 use case；骨架已提供 `ApplicationContextTest` 驗證 context 啟動，不要再寫新的 context 測試）** → adapter-web → Cucumber 驗收。bootstrap 組裝必須排在 adapter-web 之前，否則 Controller 會因找不到 use case bean 而讓整個應用程式 context 啟動失敗。每個任務必須可用一個或多個測試類別驗證。

輸出 **一個** 檔案區塊 `=== FILE: specs/{{ feature_id }}-{{ feature_slug }}/tasks.md ===`，格式嚴格如下（系統會解析）：

```
# Tasks {{ feature_id }} — <功能名稱>

## T1 <任務標題>
- 說明：<要做什麼，列出要建立的類別完整路徑>
- 測試：<要建立的測試類別完整路徑與驗證重點>

## T2 <任務標題>
- 說明：...
- 測試：...
```

不要輸出其他段落。
