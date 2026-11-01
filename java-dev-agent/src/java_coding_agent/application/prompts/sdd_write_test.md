你正在以 TDD 實作任務，現在是 **RED 階段：只寫測試**。

目前任務：
<task>
## {{ task.id }} {{ task.title }}
- 說明：{{ task.description }}
- 測試：{{ task.test_hint }}
</task>

plan.md 摘要可參考：
<plan>
{{ plan_md }}
</plan>

目前專案中已由先前任務產生的檔案（完整內容）：
<existing_files>
{{ existing_files }}
</existing_files>

骨架已提供的檔案（只列路徑，內容不可修改）：
{{ scaffold_paths }}

要求：
- **只輸出檔案區塊**，區塊外最多 3 行說明；不要逐步分析或自問自答。
- 只輸出 **測試** 檔案（`src/test/java/...` 或 `src/test/resources/features/*.feature` 與其步驟定義），使用檔案區塊格式。
- 測試必須能編譯失敗或執行失敗（因為實作尚不存在或尚未完成），這是預期的。
- 使用 JUnit Jupiter 6（`org.junit.jupiter.api`）、AssertJ（`org.assertj.core.api.Assertions.assertThat`）、Mockito。
- domain/application 測試不得使用 Spring。web 測試用 `@WebMvcTest(<Controller>.class)` 並以 `@MockitoBean` 模擬 inbound port。
- persistence 測試**不要自己宣告 Testcontainers 容器**（`@ServiceConnection` 只能放在 `@Bean` 方法上，放在欄位會編譯失敗；也不要加 `@Testcontainers`/`@Container`）。骨架已在 `adapter-persistence/src/test/java/{{ base_package_path }}/adapter/persistence/TestcontainersConfiguration.java` 提供容器設定，請照以下骨架撰寫：

```java
package {{ base_package }}.adapter.persistence;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;
import org.springframework.boot.jdbc.test.autoconfigure.AutoConfigureTestDatabase;
import org.springframework.context.annotation.Import;

@DataJpaTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@Import({JpaSaveProfilePort.class, JpaLoadProfilePort.class, JpaDeleteProfilePort.class, ProfileEntityConverter.class})
class JpaProfilePortsTest {  // 容器設定已由 PersistenceTestApplication 掛載

    @Autowired private JpaSaveProfilePort saveProfilePort;
    @Autowired private JpaLoadProfilePort loadProfilePort;
    @Autowired private JpaDeleteProfilePort deleteProfilePort;
    @Autowired private org.springframework.boot.jpa.test.autoconfigure.TestEntityManager entityManager;

    @Test
    void should_delete_profile_by_id() {
        Profile saved = saveProfilePort.save(/* 建立 Profile */);
        entityManager.flush();
        entityManager.clear();            // 清掉一級快取，否則刪除後 findById 仍會回傳快取中的實體

        deleteProfilePort.deleteById(saved.id());
        entityManager.flush();
        entityManager.clear();

        assertThat(loadProfilePort.findById(saved.id())).isEmpty();
    }
}
```

**唯一約束違反（重複 email）測試：`assertThatThrownBy(() -> port.save(duplicate))` 必須是該測試方法的最後一行。** 兩筆資料必須使用**同一個 email 字串常值**（例如 `String email = "dup@example.com";` 兩筆共用），只有 id 不同；不要用一個寫死 `test@example.com` 的 helper 搭配另一個不同的 email，那樣根本不會違反唯一約束。 斷言的例外型別是 **domain 例外**（如 `DuplicateEmailException`），不是 `DataIntegrityViolationException`（Port 實作會轉換）。 PostgreSQL 在約束違反後整個測試交易已中止（`current transaction is aborted`），之後任何 `flush()`、查詢或斷言都會失敗；「第一筆仍存在、第二筆未寫入」請勿在同一方法內驗證。

**每個會改變資料的測試步驟之後都要 `entityManager.flush(); entityManager.clear();` 再查詢**，這是 `@DataJpaTest` 單一交易的必要做法。
```java
```

- Cucumber 步驟類別固定骨架（不要加 `@Component`；回應一律存成 `ResponseEntity<String>`，狀態碼用 `.getStatusCode().value()` 比較整數）：
```java
package {{ base_package }}.bdd;

import static org.assertj.core.api.Assertions.assertThat;

import io.cucumber.java.zh_tw.假設;
import io.cucumber.java.zh_tw.當;
import io.cucumber.java.zh_tw.那麼;
import io.cucumber.java.zh_tw.並且;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.resttestclient.TestRestTemplate;
import org.springframework.http.*;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

public class PersonalProfileSteps {
    @Autowired private TestRestTemplate restTemplate;
    private final ObjectMapper objectMapper = new ObjectMapper();
    private ResponseEntity<String> lastResponse;
    private String lastId;

    private ResponseEntity<String> send(HttpMethod method, String path, String json) {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        return restTemplate.exchange(path, method, new HttpEntity<>(json, headers), String.class);
    }

    @當("我以 JSON {string} 呼叫 POST {string}")
    public void post(String json, String path) {
        lastResponse = send(HttpMethod.POST, path, json);
        if (lastResponse.getStatusCode().value() == 201 && lastResponse.getBody() != null) {
            JsonNode node = objectMapper.readTree(lastResponse.getBody());
            lastId = node.path("id").asText();
        }
    }

    @當("我呼叫 DELETE 剛建立的資料")
    public void delete() {
        lastResponse = send(HttpMethod.DELETE, "/profiles/" + lastId, null);
    }

    @那麼("回應狀態碼為 {int}")
    public void statusIs(int expected) {
        assertThat(lastResponse.getStatusCode().value()).isEqualTo(expected);
    }

    @並且("回應 JSON 的 {string} 為 {string}")
    public void jsonFieldIs(String field, String expected) {
        JsonNode node = objectMapper.readTree(lastResponse.getBody());
        assertThat(node.path(field).asText()).isEqualTo(expected);
    }
}
```
  **所有場景共用同一個資料庫、不會自動清空**：每個場景（包含 PUT 要更新成的新 email）都要用不同的 email（例如 `create@example.com`、`update-new@example.com`），否則「成功更新」會因撞到前一個場景建立的 email 而回 409。
  **前置步驟「已存在 ID 為 {string} 的個人資料」不可用 POST 建資料**：API 會自己產生 id，之後用固定 UUID 查詢/更新/刪除一律 404。請在步驟類別 `@Autowired` 應用層的 outbound `SaveProfilePort`（或 JPA repository），直接以該 id 建立 domain 物件並儲存；或改寫 .feature 為「已存在一筆個人資料」，由 POST 回應記下 id 給後續步驟使用。
  步驟類別與**每個步驟方法都必須是 `public`**（Cucumber 只掃描 public 方法，package-private 的 `void step()` 會讓所有步驟 undefined）。骨架的 `SmokeSteps` 已定義「煙霧測試呼叫 GET {string}」「煙霧測試回應狀態碼為 {int}」，你的步驟文字不可與之相同（Cucumber 會因 DuplicateStepDefinitionException 全部失敗），也不要修改 SmokeSteps。
  `.feature` 每個步驟必須寫在同一行、以關鍵字開頭（假設/當/那麼/並且/而且），Examples 關鍵字是 `例子:`。步驟文字中不要直接寫 URL 路徑（`/` 在 Cucumber Expressions 是「替代選項」符號），把路徑放進引號成為 `{string}` 參數，例如 `當 我呼叫 GET "/profiles/123"`。步驟定義的文字必須與 .feature 逐字相同（含標點、空格、冒號）。 `.feature` 裡寫的是**具體值**（例如 `當 我以 JSON "{...}" 呼叫 POST "/profiles"`），不可出現 `{string}`/`{int}` 佔位；所有參數值（包含 UUID、識別碼）都要用雙引號包住。
- Cucumber 步驟定義放在 `bootstrap/src/test/java/{{ base_package_path }}/bdd/`，使用 `io.cucumber.java.zh_tw.*` 註解（假設、當、那麼、並且），以 `TestRestTemplate` 呼叫 API。回應 body 一律以 `String.class` 接收，再以 `tools.jackson.databind.ObjectMapper#readTree` 解析 （不要用 `getForEntity(url, Map.class)` 指派給 `ResponseEntity<Map<String, Object>>`，會有泛型推論編譯錯誤）。骨架已提供 `CucumberSpringConfiguration` 與 `CucumberTest` runner，不要重複建立。
- 基底套件 `{{ base_package }}`。
