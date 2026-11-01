Gradle 測試執行失敗，請修正。這是第 {{ attempt }} 次修正（最多 {{ max_attempts }} 次）。{% if stuck %}**警告：這個錯誤與上一次完全相同，代表上一次輸出的修改沒有任何效果（可能只是重複輸出相同內容）。** 這次必須：(1) 逐行對照錯誤訊息與下方既有檔案找出真正原因；(2) 重新輸出「失敗的測試類別」與「被測類別」兩者的完整檔案，內容必須與既有檔案不同；(3) 若測試本身與 plan 衝突，可修改測試並在區塊外說明。{% elif attempt > 1 %}若錯誤與上一次相同，表示上次的修法無效，必須換一種方法，不要重複同樣的修改。{% endif %}

目前任務：
<task>
## {{ task.id }} {{ task.title }}
- 說明：{{ task.description }}
</task>

Gradle 錯誤摘要：
<gradle_output>
{{ build_output }}
</gradle_output>

目前專案中已存在的檔案（完整內容）：
<existing_files>
{{ existing_files }}
</existing_files>

骨架已提供的檔案（只列路徑，內容不可修改）：
{{ scaffold_paths }}

要求：
- **範圍限制**：只建立或修改「目前任務」所屬模組的檔案；不要提前建立其他模組的類別（例如在 application 任務中建立 JPA Entity、Controller，或修改 bootstrap 組態）。`UseCaseConfiguration` 的 `@Bean` 只能在 bootstrap 組裝任務中加入，且其依賴的 adapter 實作必須已經存在，否則 Spring context 會啟動失敗。
- 骨架檔案（CucumberSpringConfiguration、CucumberTest、ArchitectureTest、TestcontainersConfiguration、Application、build 檔）不可修改，系統會拒絕。
- **輸出格式嚴格限制**：直接輸出要修改或新增的完整檔案（檔案區塊格式），最後最多用 3 行說明修了什麼。**禁止**在輸出中逐步分析、自問自答或列出多種可能性；分析請在心中完成。超過 3 行的說明會被視為無效輸出。
- 找出造成編譯或測試失敗的根因，輸出需要修改或新增的 **完整檔案**（檔案區塊格式）。
- 不可刪除或弱化測試來讓測試通過，除非測試本身與 spec 衝突，且需在區塊外說明理由。
- 不可修改 `build.gradle.kts` 或 `settings.gradle.kts`。
- `duplicate class` / `reference to X is ambiguous`：表示有兩個同名類別（通常是放錯目錄或 port.in 與 port.out 同名）。請用 `=== DELETE FILE: <錯誤路徑> ===` 刪除多餘檔案，或重新命名其中一個並更新所有引用。
- `annotation type not applicable to this kind of declaration` 於 `@ServiceConnection`：表示你把 `@ServiceConnection` 放在欄位上。請刪除測試中自行宣告的容器欄位，改為 `@Import(TestcontainersConfiguration.class)`（骨架已提供，位於同模組測試套件）。
- `Failed to load ApplicationContext` 於 `@DataJpaTest`：通常是 `@Import` 少了 Port 實作或轉換器類別，或缺少 `@AutoConfigureTestDatabase(replace = Replace.NONE)`。
- 煙霧測試 `不存在的路徑回傳 404` 出現 `expected: 404 but was: 500`：你的 `@RestControllerAdvice` 攔截了 `Exception.class`（或 `RuntimeException.class`）。請移除該 catch-all handler，只處理明確的例外型別。
- `Unable to find a @SpringBootConfiguration` 於 `@WebMvcTest`：骨架已提供 `adapter.web.WebTestApplication`；若不存在請在 `adapter-web/src/test/java/.../adapter/web/WebTestApplication.java` 建立 `@SpringBootApplication class WebTestApplication {}`。
- `relation \"xxx\" does not exist` 於 `@DataJpaTest`：schema 未建立。骨架的 `adapter-persistence/src/test/resources/application.yml` 已設定 `ddl-auto: create-drop`；若仍發生，確認 Entity 位於 `adapter.persistence` 套件且 `@Table(name=...)` 與查詢一致。
- `@DataJpaTest` 中「刪除後查詢仍找得到」（`Expecting an empty Optional but was containing value`）：同一交易內的一級快取造成。測試注入 `org.springframework.boot.jpa.test.autoconfigure.TestEntityManager`，在刪除後呼叫 `entityManager.flush(); entityManager.clear();` 再查詢；或 Port 實作改用 `repository.deleteById(id); repository.flush();`。不要為此改動 Spring 設定或骨架。
- `'void' type not allowed here` 於測試：對 **void** 方法使用了 `when(mock.voidMethod()).thenThrow(...)`。Mockito 對 void 方法必須寫 `doThrow(new X()).when(mock).voidMethod(any());`（或 `doNothing()`），驗證呼叫用 `verify(mock).voidMethod(...)`。
- `package com.fasterxml.jackson.databind does not exist`：Spring Boot 4 用 Jackson 3，改 import `tools.jackson.databind.ObjectMapper`（`@WebMvcTest` 可直接 `@Autowired`）。
- `package org.springframework.boot.testcontainers... does not exist` 或 `org.springframework.hateoas` 於 adapter-web：該模組沒有這些相依，請移除相關 import 與用法。
- `CucumberTest`/煙霧測試出現 `Failed to load ApplicationContext` 且 `Caused by: UnsatisfiedDependencyException ... Controller ... No qualifying bean of type '...UseCase'`：Controller 需要的 use case 尚未在 bootstrap 組裝。請在 `bootstrap/src/main/java/.../bootstrap/UseCaseConfiguration.java`（允許修改）加入對應 `@Bean` 方法，以建構子注入 outbound port 實作（adapter-persistence 的 `@Component`）。
- `No transactional EntityManager found, is your test running in a transaction?`：測試類別或方法被標成 `@Transactional(propagation = Propagation.NOT_SUPPORTED)` 卻又使用 `TestEntityManager`。移除該註解（保留 `@DataJpaTest` 預設交易），並在每次會改資料的動作後 `entityManager.flush(); entityManager.clear();`。
- 唯一性測試 `Expecting code to raise a throwable`（重複 email 未拋例外）：`repository.save()` 在測試交易內不會立即寫入資料庫，唯一約束不會觸發。Port 實作必須改用 `repository.saveAndFlush(entity)`，並把 `DataIntegrityViolationException` 轉成 domain 例外。
- `BeanDefinitionOverrideException: Invalid bean definition with name 'xxxUseCase'`：同一個 bean 被定義兩次。常見原因：application 的 Service 類別被加上 `@Service`/`@Component`（不允許，application 禁用 Spring）、或存在第二個 `@Configuration`（如 `ApplicationConfig`）重複宣告 `@Bean`。請只保留 `UseCaseConfiguration` 中的一份 `@Bean`，刪除多餘的類別或註解。
- bootstrap 的 `@SpringBootTest` 出現 `Unable to determine Dialect without JDBC metadata` / `Failed to determine a suitable driver class`：測試缺少資料庫，請加上 `@Import(TestcontainersConfiguration.class)`（`{{ base_package }}.bootstrap.TestcontainersConfiguration`，骨架提供）。
- `@WebMvcTest` 中多個端點 `Status expected:<2xx/404> but was:<500>`：最常見是 `@PathVariable` 名稱與路徑樣板不一致（`/{id}` 卻寫 `@PathVariable String profileId`），請寫 `@PathVariable("id") String profileId`；其次是 `@RestControllerAdvice` 未處理該 domain 例外。
- `cannot find symbol` 且 `symbol: class Import`（或其他註解類別如 `Autowired`、`Test`、`Bean`）：缺少 import 陳述式。`@Import` 是 `org.springframework.context.annotation.Import`，`@Bean`/`@Configuration` 在 `org.springframework.context.annotation`。請補上對應 import。
- 單元測試 `NullPointerException ... because "this.xxxPort" is null`：受測 Service 未以 mock 建構。請在測試類別加 `@ExtendWith(MockitoExtension.class)`，port 欄位用 `@Mock`，受測物件用 `@InjectMocks`（或在 `@BeforeEach` 以建構子手動 `new Service(mockA, mockB)`）。
- `Only void methods can doNothing()!`：對有回傳值的方法用了 `doNothing()`，請改成 `when(mock.method(any())).thenReturn(...)`。
- `Identifier of entity ... must be manually assigned before calling 'persist()'`：Entity 沒有 `@GeneratedValue`（正確，id 由 domain 提供），但建立 Entity 時沒有設定 id。轉換器 `toEntity` 必須把 `profile.id().value()` 寫入 Entity 的 `@Id` 欄位；測試中直接 `new ProfileEntity(...)` 也必須帶入 UUID id。
- Cucumber `UndefinedStepException: The step '...' is undefined`：feature 檔的步驟文字與步驤定義不一致。請以錯誤訊息中的**原文**新增步驟方法：引號內的值用 `{string}`，數字用 `{int}`，例如 feature 寫 `當 我發送 POST 請求至 "/profiles"` → `@當("我發送 POST 請求至 {string}")`；DataTable/DocString 步驤要加對應參數（`io.cucumber.datatable.DataTable` 或 `String docString`）。不要改 feature 檔來迎合步驟，除非步驟文字本身有誤。 **步驟註解預設是 Cucumber Expressions，不是正規表示式**：`"([^\"]*)"` 這種寫法不會匹配（括號是可選文字語法）。請改用 `{string}`/`{int}`；若堅持用 regex，整個樣板必須以 `^` 開頭、`$` 結尾。
- Cucumber 步驟類別**不要**自行宣告 `PostgreSQLContainer` 或 `@ServiceConnection`；容器由骨架的 `CucumberSpringConfiguration` 提供，步驟類別只需建構子注入 `TestRestTemplate`。
- `PreconditionViolationException: arguments array must not be null` 於 `@ParameterizedTest`：JUnit 6 的 `@ValueSource` 不能含 `null`，且 `@MethodSource` 不可回傳 null。無效值測試請用 `@NullAndEmptySource` + `@ValueSource(strings = {"  ", "bad"})`，或 `@MethodSource` 回傳 `Stream.of(Arguments.of(...))`。
- MockMvc / TestRestTemplate 預期 200/201 卻得到 400：最常見是 `@Valid` 的 Request DTO 要求測試 JSON 裡沒有的欄位，例如把路徑上的 `id` 也放進 PUT 的 body 並加 `@NotBlank`。資源識別碼只從 `@PathVariable` 取得，body DTO 只含可修改欄位；並確認 record 欄位名稱與測試 JSON key 完全一致、沒有把 `IllegalArgumentException` 誤映成 400 的流程被觸發。
- `inference variable T has incompatible equality constraints Map<String,Object>,Map`（`restTemplate.getForEntity(url, Map.class)` 指派給 `ResponseEntity<Map<String, Object>>`）：改用 `restTemplate.exchange(url, HttpMethod.GET, null, new ParameterizedTypeReference<Map<String, Object>>() {})`，或以 `String.class` 接回應再用 `tools.jackson.databind.ObjectMapper` `readTree` 解析；`assertThat(map).containsKey(...)` 需要 `Map<String, Object>` 而非 `Map<?, ?>`。
- `call to this must be first statement in constructor` / `cannot find symbol rawValue` 於 record：精簡建構子（compact constructor）沒有參數列也不能呼叫 `this(...)`；要正規化就直接對同名元件變數賦值，例如 `public DisplayName { value = (value == null) ? "" : value.strip(); }`。若需要不同名稱的輸入，改寫成一般的 canonical constructor `public DisplayName(String value) { this.value = ...; }`。
- `X is not public in ...adapter.persistence; cannot be accessed from outside package`（UseCaseConfiguration 參數型別用了 `*Impl`/`Jpa*Port`）：`@Bean` 方法的參數一律用 application 的 **port 介面**型別（如 `SaveProfilePort`、`LoadProfilePort`），adapter 實作類別保持 package-private，由 Spring 依介面注入；不要 import adapter 類別，也不要把 adapter 類別改成 public。
- UseCaseConfiguration `cannot find symbol` 某個 `*Service` 或 `*Port`：代表前面任務漏寫了該 use case 實作或 port。請在同一輸出中於正確模組補上缺少的檔案（application/usecase 的 Service、application/port/out 的 Port、adapter-persistence 的實作），**不要**改成只接線已存在的那一個。
- TestRestTemplate 常見編譯錯誤：`HttpStatusCode` 沒有 `toHttpStatus()`，比較狀態碼用 `response.getStatusCode().value()` 或 `HttpStatus.valueOf(response.getStatusCode().value())`；`restTemplate.delete(...)` 回傳 void，要拿回應請用 `restTemplate.exchange(path, HttpMethod.DELETE, null, Void.class)`；回應 body 先以 `String.class` 接再用 `ObjectMapper.readTree`。
- `reference to HttpEntity is ambiguous`（`new HttpEntity<Void>(null)`）：改成 `new HttpEntity<Void>((Void) null)`，或直接把 `exchange(...)` 的第三個參數寫成 `null`（不要包成 HttpEntity）。
- 唯一性測試 `Expecting code to raise a throwable` 且 Port 實作用了 `EntityManager.merge/persist`：直接操作 EntityManager 不會立刻 flush，也不會轉成 `DataIntegrityViolationException`。改成注入 Spring Data `JpaRepository` 並呼叫 `repository.saveAndFlush(entity)`。
- `no suitable method found for any((p)->...)`：Mockito 的 `any()` 不接受 lambda；要以條件比對參數請用 `argThat(p -> ...)`（`org.mockito.ArgumentMatchers.argThat`），或用 `ArgumentCaptor` 擷取後再斷言。
- `no instance(s) of type variable(s) X exist so that Profile conforms to Optional<Profile>`（`orElseThrow` 的回傳型別不符）：Service 方法的回傳型別必須與 UseCase 介面宣告一致。介面若宣告 `Optional<Profile>` 就直接 `return port.findById(id);`；介面若宣告 `Profile` 才用 `orElseThrow(() -> new ProfileNotFoundException(id))`。不要在介面與實作各自定義不同的 `Profile` 巢狀型別。
- Cucumber `expected: 404 but was: 500`：`WebExceptionHandler`（`@RestControllerAdvice`）要把 `ProfileNotFoundException` 映成 404、`DuplicateEmailException` 映成 409、`IllegalArgumentException`/`InvalidEmailException`/`MethodArgumentNotValidException` 映成 400；路徑 id 若不是合法 UUID（如 `non-existent-id`）會先在 `UUID.fromString` 丟 `IllegalArgumentException`，請在 handler 映成 404 或 400，並讓 .feature 使用合法 UUID。
- Cucumber `expected: 201 but was: 409`：所有場景共用同一個資料庫（context 不會在場景間重建），每個場景要用**不同**的 email（例如加上 UUID 後綴），或在 `@Before` hook 清空資料表（注入 repository 呼叫 `deleteAll()`）。
- Cucumber 回 500 且訊息為 `Invalid UUID string: profiles`：步驤類別從路徑切出的 id 取錯片段（`split("/")[1]` 拿到 `profiles`）。請用最後一段 `path.substring(path.lastIndexOf('/') + 1)`，或把「假設 系統中存在 ID 為 {string}」步驟建立的 id 存在欄位中直接使用；Scenario Outline 的 `<id>` 必須是合法 UUID。
- `Expecting actual throwable to be an instance of: DataIntegrityViolationException but was: DuplicateEmailException`：這是**測試寫錯**。Port 實作依規範必須把 `DataIntegrityViolationException` 轉成 domain 例外，所以請改測試為 `.isInstanceOf(DuplicateEmailException.class)`，不要把 Port 改成丟出 Spring 例外。
- `cannot infer type arguments for ResponseEntity<>`：建構子順序是 `new ResponseEntity<>(body, headers, status)`（標頭在狀態碼之前），或直接用 `ResponseEntity.status(code).headers(h).body(b)`。
- `no suitable method found for readTree(CAP#1)`：`lastResponse` 被宣告成 `ResponseEntity<?>`，body 型別不明。欄位改成 `ResponseEntity<String> lastResponse`，呼叫 `restTemplate.exchange(..., String.class)` 接回應，再 `objectMapper.readTree(lastResponse.getBody())`。
- `to contain the instance (i.e. compared with ==) ... but did not`：從資料庫載回的是新物件，不能用 `containsSame`/`isSameAs`；改用 `hasValueSatisfying(p -> assertThat(p.id()).isEqualTo(saved.id()))` 或 `usingRecursiveComparison()`，或讓 domain 類別正確實作 `equals/hashCode`（record 已內建）後用 `contains(saved)`。
- 想改 `CucumberSpringConfiguration` / `CucumberTest` / `application.yml` 等骨架檔：**不要**。它們已正確設定 `@SpringBootTest(RANDOM_PORT)`、`@AutoConfigureTestRestTemplate`、Testcontainers 與 glue 套件；需要的狀態（最後回應、建立的 id）請放在你自己的步驟類別欄位，需要的 bean（repository、ObjectMapper）直接在步驟類別 `@Autowired`；錯誤回應格式請在 `adapter-web` 的 `@RestControllerAdvice` 處理。
- `return type Optional<...Service.Profile> is not compatible with Optional<...UseCase.Profile>` / `does not override abstract method`：介面與實作各自定義了同名巢狀型別。刪掉巢狀的 `record Profile`，統一 `import` domain 的 `Profile`。
- `cannot find symbol: method findById(Class,...) location: TestEntityManager` → 用 `entityManager.find(Entity.class, id)`；`failBecauseExpected(...)` 不存在 → 用 `assertThatThrownBy(() -> ...).isInstanceOf(X.class)`，不要在 try/catch 裡手動 fail。
- Cucumber「假設 系統中存在 ID 為 {string} 的個人資料」之後查詢/更新/刪除都回 404、重複 email 卻回 201：前置步驟沒有真的把那個 id 的資料存進 DB。POST API 會自己產生 id，無法指定；請在步驟類別 `@Autowired` 應用層的 `SaveProfilePort`（或 `CreateProfileUseCase`）直接以該 id 建立 `Profile`，或改成「用 POST 建立並把回傳的 id 存到欄位 `lastId`」，之後 GET/PUT/DELETE 一律用 `lastId` 組路徑，不要用 feature 裡的字面 id。
- `Expecting ... DataIntegrityViolationException but was: org.hibernate.exception.ConstraintViolationException`：直接用 `entityManager.persist/flush` 不會經過 Spring 的例外轉換。要測唯一約束請呼叫 Port（`saveProfilePort.save(...)`，斷言 domain 例外 `DuplicateEmailException`），或改斷言 `jakarta.persistence.PersistenceException`。
- 所有 Cucumber 步驟都 `undefined` 但步驟文字明明一致：步驟類別必須是 `public class`（package-private 的類別不會被 Cucumber 掃描到），且套件要在 glue 套件 `...bdd` 底下。
- `Failed to instantiate [...Repository]: Specified class is an interface`：不要把 Spring Data repository 介面放進 `@Import`；`@DataJpaTest` 會自動註冊 repository，`@Import` 只放 Port 實作與 Converter 等一般類別。
- `current transaction is aborted, commands ignored until end of transaction block`（PostgreSQL）：唯一約束違反後，同一個測試交易已失效，之後任何查詢都會失敗。唯一性測試要寫成「最後一個動作就是斷言拋例外」，不要在拋例外後再查詢。**不要**對使用 `TestEntityManager` 的測試加上 `@Transactional(propagation = NOT_SUPPORTED)`，那會造成 `No transactional EntityManager found`。
- `@WebMvcTest` 中 domain 例外（如 `ProfileNotFoundException`）直接以 `ServletException` 冒出而不是 404：`@RestControllerAdvice` 沒有被載入。請確認它是 `public class`、位於 `adapter.web` 套件（或其子套件），並以 `@WebMvcTest(controllers = ProfileController.class)` + `@Import(ProfileExceptionHandler.class)` 明確匯入。
- `@WebMvcTest` 更新/建立端點回 400 但測試預期 2xx：請求 JSON 與 DTO 欄位或驗證註解不一致（例如欄位名稱、`@NotBlank` 對選填欄位）。對照測試送出的 JSON 調整 DTO，不要移除必要驗證。
- `cannot find symbol: method any()/when()/verify()/mock()/assertThat()/assertThatThrownBy()`：缺少 static import。Mockito：`import static org.mockito.ArgumentMatchers.*; import static org.mockito.Mockito.*;`；AssertJ：`import static org.assertj.core.api.Assertions.*;`。
- bootstrap 測試 `Failed to load ApplicationContext ... classes = [...UseCaseConfiguration]` 且 `UnsatisfiedDependencyException`：測試寫成 `@SpringBootTest(classes = UseCaseConfiguration.class)`，只載入組態類別而沒有 JPA/adapter bean。請改為不帶 classes 的 `@SpringBootTest`（會找到骨架的 `Application`）並加 `@Import(TestcontainersConfiguration.class)`。
- `hasMessageContaining(...)` / `hasMessage(...)` 斷言失敗：例外的實際訊息與測試預期不一致。請在**例外類別**中統一訊息格式（例如 `"Profile not found: " + id.value()`），並讓測試只斷言穩定的關鍵片段（如 `isInstanceOf(ProfileNotFoundException.class)` 加 `hasMessageContaining(id.value().toString())`），不要比對整句。
- `package io.restassured does not exist` / `org.springframework.boot.test.web.client does not exist`：classpath 沒有 RestAssured，且 Boot 4 的 `TestRestTemplate` 位於 `org.springframework.boot.resttestclient.TestRestTemplate`。Cucumber 步驟請以建構子注入 `TestRestTemplate` 呼叫 API，不要用 RestAssured。
- 常見根因對照：`ObjectOptimisticLockingFailureException` / `StaleObjectStateException` 於 save 時 → Entity 同時有 `@GeneratedValue` 又被轉換器指定了 id，請移除 `@GeneratedValue` 並讓 Entity 實作 `Persistable<ID>`（`isNew()` 以 `@Transient` 欄位判斷）；`No qualifying bean` 於 `@DataJpaTest` → 以 `@Import` 匯入需要的 adapter 類別；`cannot find symbol` 於 Spring Boot 測試註解 → 對照 System Prompt 的 Spring Boot 4 套件速查。
- 基底套件 `{{ base_package }}`。
- 唯一性測試 `Expecting code to raise a throwable`，而 Port 已經用 `saveAndFlush` 並轉換 `DataIntegrityViolationException`：問題幾乎一定在**測試本身**——兩筆資料的 email 其實不同（常見：helper 寫死 `"test@example.com"`，第二筆卻傳 `"dup@test.com"`）。請修正測試，讓兩筆資料共用同一個 email 字串常值、只有 id 不同；不要再改 Port。
- 更新測試丟出 `DuplicateEmailException`／`ObjectOptimisticLockingFailureException`：以同一個 id 重新 `new Entity(...)` 會因 `isNew() == true` 被當成 INSERT 撞主鍵。Port 的 `save` 應先 `repository.findById(id)`，存在就更新受管 Entity 的欄位再 `saveAndFlush`，不存在才建立新 Entity。
- Cucumber 更新場景 `expected: 200 but was: 409`（PUT 回 409）：不是 email 真的重複，而是 Save Port 把「既有 id」的 Entity 當成新資料 INSERT（`Persistable.isNew()` 回 true）撞到主鍵，`DataIntegrityViolationException` 被一律轉成 `DuplicateEmailException` → 409。修法：Save Port 先 `repository.findById(id)`，存在就更新受管 Entity 欄位（setEmail/setDisplayName）再 `saveAndFlush`；只有找不到時才建立新 Entity。另外確認前置步驟用的 email 與其他場景不同（多個場景共用同一個資料庫）。
- `Expecting a throwable with cause being an instance of: DataIntegrityViolationException but current throwable has no cause`：這是**測試寫錯**。domain 例外不需要帶 Spring 例外當 cause；把測試裡的 `.hasCauseInstanceOf(...)`/`.hasRootCauseInstanceOf(...)` 刪掉，只斷言 `isInstanceOf(DuplicateEmailException.class)`。
- `cannot find symbol: method getPathSegments() location: class URI`：`java.net.URI` 沒有這個方法。取 Location 最後一段 id 請用 `String path = uri.getPath(); String id = path.substring(path.lastIndexOf('/') + 1);`。Spring 7 的 `HttpHeaders` 也沒有 `containsKey`，請用 `containsHeader("Location")` 或 `getFirst("Location")`。
- Cucumber `expected: 201 but was: 415`（Unsupported Media Type）：POST/PUT 的 `HttpEntity` 沒有設定 Content-Type。請 `HttpHeaders headers = new HttpHeaders(); headers.setContentType(MediaType.APPLICATION_JSON);` 再 `new HttpEntity<>(json, headers)`。
- 所有測試都 `NullPointerException ... because "this.xxxPort" is null`（Port 在 service 內是 null）：`@InjectMocks` 的欄位**不要寫初始化**（`= new XService(port)` 會在 @Mock 注入前執行，拿到 null）。改成 `@InjectMocks private XService service;`（宣告具體類別，不是介面），或在 `@BeforeEach` 內 `service = new XService(port);`。
