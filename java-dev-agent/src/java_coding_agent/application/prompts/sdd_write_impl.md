你正在以 TDD 實作任務，現在是 **GREEN 階段：寫最少量實作讓測試通過**。

目前任務：
<task>
## {{ task.id }} {{ task.title }}
- 說明：{{ task.description }}
- 測試：{{ task.test_hint }}
</task>

plan.md：
<plan>
{{ plan_md }}
</plan>

本任務的測試檔（必須讓這些測試通過）：
<tests>
{{ test_files }}
</tests>

目前專案中已存在的檔案（完整內容；可修改，但修改時必須輸出完整檔案）：
<existing_files>
{{ existing_files }}
</existing_files>

骨架已提供的檔案（只列路徑，內容不可修改）：
{{ scaffold_paths }}

要求：
- **範圍限制**：只建立或修改「目前任務」所屬模組的檔案；不要提前建立其他模組的類別（例如在 application 任務中建立 JPA Entity、Controller，或修改 bootstrap 組態）。`UseCaseConfiguration` 的 `@Bean` 只能在 bootstrap 組裝任務中加入，且其依賴的 adapter 實作必須已經存在，否則 Spring context 會啟動失敗。
- 骨架檔案（CucumberSpringConfiguration、CucumberTest、ArchitectureTest、TestcontainersConfiguration、Application、build 檔）不可修改，系統會拒絕。
- **只輸出檔案區塊**，區塊外最多 3 行說明；不要逐步分析或自問自答。
- 輸出 **實作** 檔案（`src/main/java/...`），必要時可修正你自己先前產生的檔案；若需調整測試，也以完整檔案輸出。
- 嚴格遵守六角形架構與套件規則；ArchUnit 會檢查。
- 若需要 Spring bean 組裝，**直接修改** `bootstrap/src/main/java/{{ base_package_path }}/bootstrap/UseCaseConfiguration.java`（骨架提供、允許修改，目前內容已列在既有檔案中），以 `@Bean` 建立 use case 並輸出完整檔案。不要另外建立其他 `@Configuration` 類別（例如 `ApplicationConfig`）。
- 基底套件 `{{ base_package }}`。
\n- 持久化 Save Port 的 `save()`：先 `repository.findById(id)`；存在就以 setter 更新那個受管 Entity 再 `saveAndFlush`，不存在才 `new` Entity。直接 `saveAndFlush(converter.toEntity(profile))` 在更新既有 id 時會因 `isNew()==true` 被當成 INSERT 撞主鍵（最後變成 409），系統會拒絕這種寫法。\n