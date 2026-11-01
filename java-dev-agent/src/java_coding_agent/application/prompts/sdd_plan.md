你正在執行 Spec-Driven Development 的第二階段：撰寫 **plan.md**（技術設計）。

spec.md：
<spec>
{{ spec_md }}
</spec>

專案資訊：
- 基底套件：`{{ base_package }}`
- 模組與套件對應（已由骨架建立，不可更動）：
  - `domain/src/main/java/{{ base_package_path }}/domain/...`
  - `application/src/main/java/{{ base_package_path }}/application/port/in/...`、`.../port/out/...`、`.../usecase/...`
  - `adapter-web/src/main/java/{{ base_package_path }}/adapter/web/...`
  - `adapter-persistence/src/main/java/{{ base_package_path }}/adapter/persistence/...`
  - `bootstrap/src/main/java/{{ base_package_path }}/bootstrap/...`（`@Configuration` 組裝 use case）
  - `bootstrap/src/test/java/{{ base_package_path }}/bdd/...`（Cucumber 步驟）、`bootstrap/src/test/resources/features/*.feature`

請輸出 **一個** 檔案區塊 `=== FILE: specs/{{ feature_id }}-{{ feature_slug }}/plan.md ===`，內容包含：

1. `## 領域模型`：Entity / Value Object（record）、不變條件、Domain Exception。
2. `## Use Cases 與 Ports`：每個 inbound port 介面（方法簽名）、outbound port 介面（方法簽名）、對應 use case 類別。
3. `## Web Adapter`：Controller、路徑、Request/Response DTO、錯誤對應（例外 → HTTP 狀態碼）。
4. `## Persistence Adapter`：JPA Entity、Spring Data Repository、Port 實作、轉換器。
5. `## Bootstrap`：`@Configuration` 中要建立的 bean。
6. `## 測試策略`：每層的測試類別與重點，Cucumber feature 檔名。
7. `## 類別清單`：表格列出每個類別的完整路徑（相對專案根目錄）與一句職責。

請遵守 System Prompt 的架構與 SOLID 規則。
