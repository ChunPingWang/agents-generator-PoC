import re

import pytest

from java_coding_agent.domain.errors import LlmOutputError
from java_coding_agent.domain.file_blocks import parse_file_blocks, render_file_blocks
from java_coding_agent.domain.models import GeneratedFile

SAMPLE = """Here are the files.

=== FILE: app-domain/src/main/java/com/acme/Member.java ===
package com.acme;
public record Member(String id) {}
=== END FILE ===

=== FILE: app-domain/src/test/java/com/acme/MemberTest.java ===
```java
package com.acme;
class MemberTest {}
```
=== END FILE ===
"""


def test_parses_multiple_blocks_and_strips_fences():
    files = parse_file_blocks(SAMPLE)
    assert [f.path for f in files] == [
        "app-domain/src/main/java/com/acme/Member.java",
        "app-domain/src/test/java/com/acme/MemberTest.java",
    ]
    assert files[0].content == "package com.acme;\npublic record Member(String id) {}\n"
    assert files[1].content == "package com.acme;\nclass MemberTest {}\n"


def test_raises_when_no_blocks():
    with pytest.raises(LlmOutputError):
        parse_file_blocks("no files here")


def test_round_trip():
    files = [GeneratedFile("a/B.java", "class B {}\n"), GeneratedFile("c.feature", "# language: zh-TW\n功能: x\n")]
    assert parse_file_blocks(render_file_blocks(files)) == files


def test_java_path_is_corrected_from_package_declaration():
    text = (
        "=== FILE: application/src/main/java/com/github/owner.repo/application/port/out/DeletePort.java ===\n"
        "package com.github.owner.repo.application.port.out;\npublic interface DeletePort {}\n=== END FILE ===\n"
    )
    [f] = parse_file_blocks(text)
    assert f.path == "application/src/main/java/com/github/owner/repo/application/port/out/DeletePort.java"


def test_non_java_or_packageless_paths_untouched():
    text = "=== FILE: bootstrap/src/test/resources/features/x.feature ===\n功能: x\n=== END FILE ===\n"
    assert parse_file_blocks(text)[0].path == "bootstrap/src/test/resources/features/x.feature"


def test_delete_blocks_are_parsed_and_allowed_alone():
    from java_coding_agent.domain.file_blocks import parse_deletions

    text = "說明\n=== DELETE FILE: a/B.java ===\n=== FILE: a/C.java ===\nclass C {}\n=== END FILE ===\n"
    assert [d.path for d in parse_deletions(text)] == ["a/B.java"]
    assert [f.path for f in parse_file_blocks(text)] == ["a/C.java"]
    assert parse_file_blocks("=== DELETE FILE: a/B.java ===") == []


def test_missing_end_marker_is_tolerated():
    text = (
        "=== FILE: specs/001-x/tasks.md ===\n# Tasks\n\n## T1 a\n- 說明：d\n"
        "=== FILE: a/B.java ===\npackage a;\nclass B {}\n"
    )
    files = parse_file_blocks(text)
    assert [f.path for f in files] == ["specs/001-x/tasks.md", "a/B.java"]
    assert files[0].content == "# Tasks\n\n## T1 a\n- 說明：d\n"
    assert files[1].content == "package a;\nclass B {}\n"


def test_mixed_end_markers_and_trailing_prose():
    text = "前言\n=== FILE: a/A.java ===\nclass A {}\n=== END FILE ===\n說明文字\n=== FILE: a/B.java ===\nclass B {}\n"
    files = parse_file_blocks(text)
    assert files[0].content == "class A {}\n" and files[1].content == "class B {}\n"


def test_cucumber_regex_captures_are_normalized_to_cucumber_expressions():
    from java_coding_agent.domain.java_paths import normalize_cucumber_steps

    src = (
        "import io.cucumber.java.zh_tw.假設;\n"
        '@假設("電子郵件 \\"([^\\"]*)\\" 在系統中尚不存在")\n'
        "public void a(String email) {}\n"
        '@當("^我發送 GET 請求至 \\"(.*?)\\"$")\n'
        "public void b(String path) {}\n"
        '@那麼("回應狀態碼應為 {int} Created")\n'
        "public void c(int code) {}\n"
    )
    out = normalize_cucumber_steps(src)
    assert '@假設("電子郵件 {string} 在系統中尚不存在")' in out
    assert '@當("我發送 GET 請求至 {string}")' in out
    assert '@那麼("回應狀態碼應為 {int} Created")' in out

    text = (
        "=== FILE: bootstrap/src/test/java/a/b/bdd/S.java ===\npackage a.b.bdd;\nimport io.cucumber.java.zh_tw.當;\n"
        '@當("我發送 POST 請求至 \\"([^\\"]*)\\"")\npublic void x(String p) {}\n'
    )
    [f] = parse_file_blocks(text)
    assert "{string}" in f.content and "([^" not in f.content


def test_spring_stereotypes_are_stripped_from_application_code():
    text = (
        "=== FILE: application/src/main/java/a/b/application/usecase/S.java ===\n"
        "package a.b.application.usecase;\n\nimport a.b.application.port.in.X;\n"
        "import org.springframework.stereotype.Service;\n"
        "import org.springframework.transaction.annotation.Transactional;\n\n"
        "@Service\n@Transactional(readOnly = true)\n"
        "public class S implements X {}\n"
    )
    [f] = parse_file_blocks(text)
    assert "org.springframework" not in f.content and "@Service" not in f.content and "@Transactional" not in f.content
    assert "import a.b.application.port.in.X;" in f.content and "public class S implements X {}" in f.content
    # adapter 模組不受影響
    text2 = (
        "=== FILE: adapter-persistence/src/main/java/a/b/adapter/persistence/P.java ===\n"
        "package a.b.adapter.persistence;\nimport org.springframework.stereotype.Component;\n@Component class P {}\n"
    )
    assert "@Component" in parse_file_blocks(text2)[0].content


def test_nullable_and_validated_annotations_are_stripped_too():
    from java_coding_agent.domain.java_paths import strip_spring_stereotypes

    src = (
        "package a.b.application.usecase;\nimport org.springframework.lang.NonNull;\n"
        "import org.springframework.validation.annotation.Validated;\n@Validated\npublic class S {\n"
        "    public S(@NonNull String x) {}\n}\n"
    )
    out = strip_spring_stereotypes("application/src/main/java/a/b/application/usecase/S.java", src)
    assert "org.springframework" not in out and "@Validated" not in out and "@NonNull" not in out
    assert "public S(String x) {}" in out


def test_data_jpa_tests_are_made_non_transactional():
    from java_coding_agent.domain.java_paths import normalize_data_jpa_tests

    src = (
        "package a.b.adapter.persistence;\n\nimport org.junit.jupiter.api.Test;\n"
        "import org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;\n\n"
        "/** doc */\n@DataJpaTest\n@Import(X.class)\nclass PortTest {\n    @Test void t() {}\n}\n"
    )
    out = normalize_data_jpa_tests("adapter-persistence/src/test/java/a/b/adapter/persistence/PortTest.java", src)
    assert "@Transactional(propagation = Propagation.NOT_SUPPORTED)" in out
    assert "@DirtiesContext(classMode = DirtiesContext.ClassMode.AFTER_EACH_TEST_METHOD)" in out
    assert out.index("@Transactional") < out.index("@DataJpaTest") < out.index("class PortTest")
    assert "import org.springframework.transaction.annotation.Transactional;" in out
    assert (
        normalize_data_jpa_tests("adapter-persistence/src/test/java/a/b/adapter/persistence/PortTest.java", out) == out
    )
    assert normalize_data_jpa_tests("adapter-web/src/test/java/a/b/adapter/web/T.java", src) == src


def test_data_jpa_tests_using_test_entity_manager_keep_transaction():
    from java_coding_agent.domain.java_paths import normalize_data_jpa_tests

    path = "adapter-persistence/src/test/java/a/b/adapter/persistence/PortTest.java"
    src = (
        "package a.b.adapter.persistence;\n\nimport org.junit.jupiter.api.Test;\n"
        "import org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;\n\n"
        "@DataJpaTest\n@Import(X.class)\nclass PortTest {\n"
        "    @Autowired private org.springframework.boot.jpa.test.autoconfigure.TestEntityManager em;\n"
        "    @Test void t() { em.flush(); }\n}\n"
    )
    out = normalize_data_jpa_tests(path, src)
    assert "NOT_SUPPORTED" not in out  # TestEntityManager 需要交易
    assert "@DirtiesContext(classMode = DirtiesContext.ClassMode.AFTER_EACH_TEST_METHOD)" in out
    assert "import org.springframework.test.annotation.DirtiesContext;" in out
    assert "import org.springframework.transaction.annotation.Transactional;" not in out
    assert normalize_data_jpa_tests(path, out) == out
    # 若 LLM（或舊版 normalizer）已加上 NOT_SUPPORTED，而測試用到 TestEntityManager，則移除之
    broken = out.replace("@DataJpaTest", "@Transactional(propagation = Propagation.NOT_SUPPORTED)\n@DataJpaTest")
    fixed = normalize_data_jpa_tests(path, broken)
    assert "NOT_SUPPORTED" not in fixed and fixed.count("@DirtiesContext(") == 1
    # 模型照抄了 @DirtiesContext 卻沒 import → 補 import，不重複加註解
    copied = (
        "package a.b.adapter.persistence;\nimport org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;\n"
        "@DirtiesContext(classMode = DirtiesContext.ClassMode.AFTER_EACH_TEST_METHOD)\n@DataJpaTest\nclass PortTest {\n"
        "    @Autowired private org.springframework.boot.jpa.test.autoconfigure.TestEntityManager em;\n}\n"
    )
    fixed_copy = normalize_data_jpa_tests(path, copied)
    assert "import org.springframework.test.annotation.DirtiesContext;" in fixed_copy
    assert fixed_copy.count("@DirtiesContext(") == 1
    # 直接注入 jakarta.persistence.EntityManager 也需要交易
    plain_em = src.replace(
        "org.springframework.boot.jpa.test.autoconfigure.TestEntityManager em", "jakarta.persistence.EntityManager em"
    )
    assert "NOT_SUPPORTED" not in normalize_data_jpa_tests(path, plain_em)
    # 方法層級、完整套件名的寫法（fix 提示曾建議過）也要移除
    method_level = out.replace(
        "    @Test void t()",
        "    @Transactional(propagation = org.springframework.transaction.annotation.Propagation.NOT_SUPPORTED)\n"
        "    @Test void t()",
    )
    fixed2 = normalize_data_jpa_tests(path, method_level)
    assert "NOT_SUPPORTED" not in fixed2 and "@Test void t()" in fixed2


def test_bootstrap_spring_boot_tests_get_testcontainers_import():
    from java_coding_agent.domain.java_paths import normalize_boot_tests

    src = (
        "package a.b.bootstrap;\n\nimport org.junit.jupiter.api.Test;\n"
        "import org.springframework.boot.test.context.SpringBootTest;\n\n@SpringBootTest\nclass WiringTest {\n}\n"
    )
    out = normalize_boot_tests("bootstrap/src/test/java/a/b/bootstrap/WiringTest.java", src)
    assert "@Import(a.b.bootstrap.TestcontainersConfiguration.class)" in out
    assert "import org.springframework.context.annotation.Import;" in out
    assert out.index("@Import(") < out.index("@SpringBootTest") < out.index("class WiringTest")
    # 已有設定者不重複；非 bootstrap 不處理
    assert normalize_boot_tests("bootstrap/src/test/java/a/b/bootstrap/WiringTest.java", out) == out
    assert normalize_boot_tests("adapter-web/src/test/java/a/b/adapter/web/T.java", src) == src


def test_cucumber_step_classes_lose_component_annotation():
    from java_coding_agent.domain.java_paths import strip_cucumber_step_stereotypes

    src = (
        "package a.b.bdd;\n\nimport io.cucumber.java.zh_tw.當;\nimport org.springframework.stereotype.Component;\n"
        "import org.springframework.beans.factory.annotation.Autowired;\n\n@Component\nclass ProfileSteps {\n"
        '    @Autowired private Object restTemplate;\n    @當("呼叫")\n    void call() {}\n}\n'
    )
    out = strip_cucumber_step_stereotypes("bootstrap/src/test/java/a/b/bdd/ProfileSteps.java", src)
    assert "@Component" not in out and "stereotype.Component" not in out
    assert "@Autowired private Object restTemplate;" in out  # 步驟類別內的注入保留
    # 非 Cucumber 測試不處理
    plain = "package a.b;\nimport org.springframework.stereotype.Component;\n@Component\nclass X {}\n"
    assert strip_cucumber_step_stereotypes("bootstrap/src/test/java/a/b/X.java", plain) == plain


def test_gherkin_zh_tw_keywords_are_normalized():
    from java_coding_agent.domain.java_paths import normalize_gherkin_zh_tw

    src = (
        "# language: zh-TW\n\n功能: 個人資料\n  情境大綱: 新增\n    給定 沒有資料\n"
        '    當 我發送 POST 請求至 "/profiles"，內容包含:\n'
        '      """json\n      {"name": "<名稱>"}\n      """\n    那麼 回應狀態碼為 201\n\n    範例:\n'
        "      | 名稱 |\n      | 王小明 |\n"
    )
    out = normalize_gherkin_zh_tw("bootstrap/src/test/resources/features/p.feature", src)
    assert "  場景大綱: 新增" in out and "    例子:" in out and "    假設 沒有資料" in out
    follow = '功能: f\n  場景: a\n    那麼 回應的狀態碼應該是 200\n    然後再次查詢該資源時，顯示名稱應為 "New"\n'
    out_f = normalize_gherkin_zh_tw("f.feature", follow)
    assert '    那麼 回應的狀態碼應該是 200\n    那麼再次查詢該資源時，顯示名稱應為 "New"\n' in out_f
    assert '當 我發送 POST 請求至 "/profiles"，內容包含:' in out  # 步驟文字中的冒號不受影響
    assert '{"name": "<名稱>"}' in out
    assert normalize_gherkin_zh_tw("x/Steps.java", src) == src
    no_lang = "功能: 測試\n  場景: a\n    當 b\n"
    assert normalize_gherkin_zh_tw("f.feature", no_lang).startswith("# language: zh-TW\n功能: 測試")


def test_unimplemented_use_cases_detects_missing_services():
    from java_coding_agent.domain.java_paths import unimplemented_use_cases

    base = "application/src/main/java/a/b/application"

    def iface(sub: str, name: str) -> str:
        return f"package a.b.application.port.{sub};\npublic interface {name} {{}}\n"

    sources = {
        f"{base}/port/in/CreateProfileUseCase.java": iface("in", "CreateProfileUseCase"),
        f"{base}/port/in/DeleteProfileUseCase.java": iface("in", "DeleteProfileUseCase"),
        f"{base}/port/in/GetProfileUseCase.java": iface("in", "GetProfileUseCase"),
        f"{base}/port/out/SaveProfilePort.java": iface("out", "SaveProfilePort"),
        f"{base}/usecase/CreateProfileService.java": (
            "package a.b.application.usecase;\npublic class CreateProfileService implements CreateProfileUseCase {}\n"
        ),
        f"{base}/usecase/ProfileQueryService.java": (
            "package a.b.application.usecase;\nfinal class ProfileQueryService\n"
            "        implements GetProfileUseCase, java.io.Serializable {\n}\n"
        ),
        "application/src/test/java/a/b/application/usecase/X.java": "class X implements DeleteProfileUseCase {}\n",
    }
    assert unimplemented_use_cases(sources) == ["DeleteProfileUseCase"]  # 測試程式碼不算實作
    assert unimplemented_use_cases({"domain/src/main/java/a/b/domain/P.java": "public record P() {}"}) == []


def test_test_only_sources_are_relocated_from_main_to_test():
    from java_coding_agent.domain.java_paths import normalize_java_path

    steps = (
        "package a.b.bdd;\n\nimport static org.assertj.core.api.Assertions.assertThat;\n"
        "import io.cucumber.java.zh_tw.當;\nclass ProfileSteps {}\n"
    )
    assert (
        normalize_java_path("bootstrap/src/main/java/a/b/bdd/ProfileSteps.java", steps)
        == "bootstrap/src/test/java/a/b/bdd/ProfileSteps.java"
    )
    junit = "package a.b.domain;\nimport org.junit.jupiter.api.Test;\nclass PT { @Test void t() {} }\n"
    assert (
        normalize_java_path("domain/src/main/java/a/b/domain/PT.java", junit)
        == "domain/src/test/java/a/b/domain/PT.java"
    )
    plain = "package a.b.domain;\npublic record P(String v) {}\n"
    assert (
        normalize_java_path("domain/src/main/java/a/b/domain/P.java", plain) == "domain/src/main/java/a/b/domain/P.java"
    )


def test_cucumber_zh_tw_annotation_aliases_are_corrected():
    from java_coding_agent.domain.java_paths import normalize_cucumber_steps

    src = (
        "package a.b.bdd;\n\nimport io.cucumber.java.zh_tw.給定;\nimport io.cucumber.java.zh_tw.當;\n"
        "import io.cucumber.java.zh_tw.然後;\nimport io.cucumber.java.zh_tw.那麼;\n\nclass S {\n"
        '    @給定("已存在 {string}")\n    void a(String s) {}\n'
        '    @然後("回應狀態碼為 {int}")\n    void b(int c) {}\n'
        '    @那麼("回應標頭 {string} 不為空")\n    void c(String h) {}\n}\n'
    )
    out = normalize_cucumber_steps(src)
    assert "然後" not in out and "給定" not in out
    assert '@假設("已存在 {string}")' in out and '@那麼("回應狀態碼為 {int}")' in out
    assert out.count("import io.cucumber.java.zh_tw.那麼;") == 1
    assert "import io.cucumber.java.zh_tw.假設;" in out


def test_gherkin_step_continuation_lines_are_folded():
    from java_coding_agent.domain.java_paths import normalize_gherkin_zh_tw

    src = (
        "# language: zh-TW\n\n功能: 個人資料\n    作為一名用戶\n    我希望能夠管理我的個人資料\n\n"
        "    場景: 查詢\n        假設 已存在一筆個人資料\n"
        '        其電子郵件為 "q@example.com"，顯示名稱為 "Query User"\n'
        '        當 我發送 GET 請求至 "/profiles/{id}"\n        那麼 回應狀態碼為 200\n'
        '        並且 回應內容包含:\n            """json\n            {"email": "q@example.com"}\n            """\n'
    )
    out = normalize_gherkin_zh_tw("f.feature", src)
    assert '        假設 已存在一筆個人資料，其電子郵件為 "q@example.com"，顯示名稱為 "Query User"\n' in out
    assert "    作為一名用戶\n    我希望能夠管理我的個人資料\n" in out  # 功能描述保留
    assert '            {"email": "q@example.com"}\n' in out  # docstring 內容不動


def test_legacy_boot3_imports_are_rewritten():
    from java_coding_agent.domain.java_paths import rewrite_legacy_imports

    src = (
        "package a.b.bdd;\n\nimport org.springframework.boot.test.web.client.TestRestTemplate;\n"
        "import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;\n"
        "import org.springframework.boot.test.context.SpringBootTest;\n"
        "import com.fasterxml.jackson.databind.ObjectMapper;\n"
        "import com.fasterxml.jackson.annotation.JsonProperty;\n"
        "import static org.assertj.core.api.Assertions.assertThat;\n"
        "import org.testcontainers.containers.PostgreSQLContainer;\n\nclass S {}\n"
    )
    out = rewrite_legacy_imports("bootstrap/src/test/java/a/b/bdd/S.java", src)
    assert "import org.springframework.boot.resttestclient.TestRestTemplate;" in out
    assert "import org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;" in out
    assert "import org.springframework.boot.test.context.SpringBootTest;" in out  # 未搬移的保留
    assert "import tools.jackson.databind.ObjectMapper;" in out
    assert "import com.fasterxml.jackson.annotation.JsonProperty;" in out  # Jackson 3 的註解仍在 com.fasterxml
    assert "import org.testcontainers.postgresql.PostgreSQLContainer;" in out
    assert "boot.test.web.client" not in out
    assert rewrite_legacy_imports("x.feature", src) == src


def test_null_http_entity_constructor_is_disambiguated():
    from java_coding_agent.domain.java_paths import fix_null_http_entity

    src = (
        "var a = new org.springframework.http.HttpEntity<Void>(null);\n"
        "var b = new HttpEntity<String>( null );\nvar c = new HttpEntity<>(null);\n"
        "var d = new HttpEntity<>(json, headers);\n"
    )
    out = fix_null_http_entity("bootstrap/src/test/java/a/b/bdd/S.java", src)
    assert "new HttpEntity<Void>((Void) null);" in out
    assert "new HttpEntity<String>((String) null);" in out
    assert out.count("new HttpEntity<Void>((Void) null);") == 2  # diamond 版本也修正
    assert "new HttpEntity<>(json, headers);" in out
    assert fix_null_http_entity("x.feature", src) == src


def test_stub_violations_flag_not_implemented_placeholders():
    from java_coding_agent.domain.java_paths import stub_violations

    stub = GeneratedFile(
        "application/src/main/java/a/b/application/usecase/UpdateProfileService.java",
        "class UpdateProfileService {\n    public void execute() {\n"
        '        throw new UnsupportedOperationException("Not implemented in this snippet due to TDD Red phase");\n'
        "    }\n}\n",
    )
    legit = GeneratedFile(
        "domain/src/main/java/a/b/domain/Profile.java",
        'class Profile {\n    void frozen() { throw new UnsupportedOperationException("Profile is immutable"); }\n}\n',
    )
    in_test = GeneratedFile("domain/src/test/java/a/b/domain/T.java", "// TODO: implement later\nclass T {}\n")
    assert stub_violations([stub, legit, in_test]) == [stub.path]


def test_adapter_top_level_types_become_public():
    from java_coding_agent.domain.java_paths import publicize_adapter_types

    src = (
        "package a.b.adapter.persistence;\n\nimport x.Y;\n\n/** doc */\n@Component\n"
        "final class JpaSaveProfilePort implements SaveProfilePort {\n"
        "    private static class Helper {}\n    record Row(String v) {}\n}\n"
    )
    out = publicize_adapter_types(
        "adapter-persistence/src/main/java/a/b/adapter/persistence/JpaSaveProfilePort.java", src
    )
    assert "\npublic final class JpaSaveProfilePort implements SaveProfilePort {" in out
    assert "    private static class Helper {}" in out and "    record Row(String v) {}" in out  # 巢狀不動
    iface = "package a.b.adapter.persistence;\ninterface ProfileJpaRepository extends JpaRepository<E, UUID> {}\n"
    assert "public interface ProfileJpaRepository" in publicize_adapter_types(
        "adapter-persistence/src/main/java/a/b/adapter/persistence/ProfileJpaRepository.java", iface
    )
    already = "package a.b.adapter.web;\npublic class C {}\n"
    assert publicize_adapter_types("adapter-web/src/main/java/a/b/adapter/web/C.java", already) == already
    # 非 adapter 模組與測試程式碼不處理
    dom = "package a.b.domain;\nrecord P() {}\n"
    assert publicize_adapter_types("domain/src/main/java/a/b/domain/P.java", dom) == dom
    assert publicize_adapter_types("adapter-web/src/test/java/a/b/adapter/web/T.java", "class T {}\n") == "class T {}\n"


def test_persistence_adapters_may_not_inject_entity_manager():
    from java_coding_agent.domain.java_paths import persistence_violations

    bad = GeneratedFile(
        "adapter-persistence/src/main/java/a/b/adapter/persistence/JpaSaveProfilePort.java",
        "package a.b.adapter.persistence;\nimport jakarta.persistence.EntityManager;\nclass JpaSaveProfilePort {}\n",
    )
    good = GeneratedFile(
        "adapter-persistence/src/main/java/a/b/adapter/persistence/JpaLoadProfilePort.java",
        "package a.b.adapter.persistence;\nimport org.springframework.data.jpa.repository.JpaRepository;\nclass X {}\n",
    )
    test_ok = GeneratedFile(
        "adapter-persistence/src/test/java/a/b/adapter/persistence/T.java",
        "import jakarta.persistence.EntityManager;\nclass T {}\n",
    )
    out = persistence_violations([bad, good, test_ok])
    assert len(out) == 1 and out[0].startswith(bad.path) and "saveAndFlush" in out[0]


def test_cucumber_step_slashes_are_escaped():
    from java_coding_agent.domain.java_paths import normalize_cucumber_steps

    src = (
        "import io.cucumber.java.zh_tw.那麼;\nclass S {\n"
        '    @那麼("Location 標頭存在且指向 /profiles/{string}")\n    void a(String s) {}\n'
        '    @當("我呼叫 GET \\\\/health")\n    void b() {}\n}\n'
    )
    out = normalize_cucumber_steps(src)
    assert '@那麼("Location 標頭存在且指向 \\\\/profiles\\\\/{string}")' in out
    assert '@當("我呼叫 GET \\\\/health")' in out  # 已跳脫的不重複處理


def test_removed_spring_apis_are_rewritten():
    from java_coding_agent.domain.java_paths import rewrite_removed_apis

    src = (
        "int a = response.getStatusCodeValue();\nint b = resp.getRawStatusCode();\n"
        "HttpStatus s = resp.getStatusCode().toHttpStatus();\n"
    )
    out = rewrite_removed_apis("bootstrap/src/test/java/a/b/bdd/S.java", src)
    assert "int a = response.getStatusCode().value();" in out
    assert "int b = resp.getStatusCode().value();" in out
    assert "HttpStatus s = resp.getStatusCode();" in out
    assert rewrite_removed_apis("x.feature", src) == src


def test_cucumber_quoted_string_params_are_unwrapped():
    from java_coding_agent.domain.java_paths import normalize_cucumber_steps

    src = (
        "import io.cucumber.java.zh_tw.假設;\nclass S {\n"
        '    @假設("已經存在一個電子郵件為 \\"{string}\\" 的個人資料")\n    void a(String s) {}\n'
        '    @當("我發送 POST 請求至 \\"{string}\\"，內容包含:")\n    void b(String s, String doc) {}\n'
        "    @那麼(\"回應 JSON 的 '{string}' 為 {string}\")\n    void c(String f, String v) {}\n}\n"
    )
    out = normalize_cucumber_steps(src)
    assert '@假設("已經存在一個電子郵件為 {string} 的個人資料")' in out
    assert '@當("我發送 POST 請求至 {string}，內容包含:")' in out
    assert '@那麼("回應 JSON 的 {string} 為 {string}")' in out


def test_adapter_port_implementations_get_component_annotation():
    from java_coding_agent.domain.java_paths import ensure_adapter_components

    path = "adapter-persistence/src/main/java/a/b/adapter/persistence/JpaSaveProfilePort.java"
    src = (
        "package a.b.adapter.persistence;\n\nimport a.b.application.port.out.SaveProfilePort;\n\n"
        "/** doc */\npublic class JpaSaveProfilePort implements SaveProfilePort {\n    void x() {}\n}\n"
    )
    out = ensure_adapter_components(path, src)
    assert "import org.springframework.stereotype.Component;" in out
    assert "@Component\npublic class JpaSaveProfilePort implements SaveProfilePort {" in out
    assert ensure_adapter_components(path, out) == out  # 已有註解不重複
    entity = "package a.b.adapter.persistence;\n@Entity\npublic class ProfileJpaEntity {}\n"
    assert ensure_adapter_components(path.replace("JpaSaveProfilePort", "ProfileJpaEntity"), entity) == entity
    repo = "package a.b.adapter.persistence;\npublic interface ProfileJpaRepository extends JpaRepository<E, UUID> {}\n"
    assert ensure_adapter_components(path.replace("JpaSaveProfilePort", "ProfileJpaRepository"), repo) == repo
    ctrl = "package a.b.adapter.web;\n@RestController\npublic class C implements SomePort {}\n"
    assert ensure_adapter_components("adapter-web/src/main/java/a/b/adapter/web/C.java", ctrl) == ctrl


def test_missing_static_imports_in_tests_are_added():
    from java_coding_agent.domain.java_paths import ensure_test_static_imports

    path = "adapter-web/src/test/java/a/b/adapter/web/ProfileControllerTest.java"
    src = (
        "package a.b.adapter.web;\n\nimport static org.mockito.Mockito.when;\n"
        "import org.springframework.test.web.servlet.MockMvc;\n\nclass ProfileControllerTest {\n"
        "    MockMvc mockMvc;\n    void t() throws Exception {\n        doNothing().when(x).execute(any());\n"
        '        mockMvc.perform(get("/profiles")).andExpect(status().isOk()).andExpect(jsonPath("$.a").value(1));\n'
        "        assertThat(1).isEqualTo(1);\n    }\n}\n"
    )
    out = ensure_test_static_imports(path, src)
    assert "import static org.mockito.Mockito.*;" in out
    assert "import static org.assertj.core.api.Assertions.*;" in out
    assert "import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;" in out
    assert "import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;" in out
    assert out.index("package a.b.adapter.web;") < out.index("import static org.mockito.Mockito.*;")
    assert ensure_test_static_imports(path, out) == out
    # 與 JUnit Assertions.* 並存時只補具名匯入，避免 fail() 模稜兩可
    junit = (
        "package a.b;\nimport static org.junit.jupiter.api.Assertions.*;\nclass T {\n"
        "    void t() { assertThat(1).isEqualTo(1); fail(); }\n}\n"
    )
    out2 = ensure_test_static_imports("domain/src/test/java/a/b/T.java", junit)
    assert "import static org.assertj.core.api.Assertions.assertThat;" in out2
    assert "org.assertj.core.api.Assertions.*" not in out2
    main = "package a.b;\nclass M { void m() { when(x); } }\n"
    assert ensure_test_static_imports("domain/src/main/java/a/b/M.java", main) == main


def test_missing_project_imports_are_added_from_type_index():
    from java_coding_agent.domain.java_paths import add_missing_project_imports, project_type_index

    base = "application/src/main/java/a/b/application"

    def src(pkg: str, decl: str) -> str:
        return f"package a.b.application.{pkg};\npublic {decl} {{}}\n"

    sources = {
        f"{base}/port/out/SaveProfilePort.java": src("port.out", "interface SaveProfilePort"),
        f"{base}/port/out/LoadProfilePort.java": src("port.out", "interface LoadProfilePort"),
        f"{base}/usecase/CreateProfileService.java": src("usecase", "class CreateProfileService"),
        "domain/src/main/java/a/b/domain/Profile.java": "package a.b.domain;\npublic record Profile() {}\n",
        # 同名型別出現在兩個套件 → 不納入索引
        "adapter-web/src/main/java/a/b/adapter/web/Mapper.java": "package a.b.adapter.web;\nclass Mapper {}\n",
        "adapter-persistence/src/main/java/a/b/adapter/persistence/Mapper.java": (
            "package a.b.adapter.persistence;\nclass Mapper {}\n"
        ),
    }
    index = project_type_index(sources)
    assert index["SaveProfilePort"] == "a.b.application.port.out.SaveProfilePort" and "Mapper" not in index
    test_src = (
        "package a.b.application.usecase;\n\nimport static org.mockito.Mockito.mock;\nimport a.b.domain.Profile;\n\n"
        "class CreateProfileServiceTest {\n"
        "    private final SaveProfilePort savePort = mock(SaveProfilePort.class);\n"
        "    private final LoadProfilePort loadPort = mock(LoadProfilePort.class);\n"
        '    String s = "LoadProfilePort in a string"; // Mapper in a comment\n'
        "    CreateProfileService service = new CreateProfileService();\n    Profile p = null;\n}\n"
    )
    out = add_missing_project_imports("application/src/test/java/a/b/application/usecase/T.java", test_src, index)
    assert "import a.b.application.port.out.SaveProfilePort;" in out
    assert "import a.b.application.port.out.LoadProfilePort;" in out
    assert out.count("import a.b.domain.Profile;") == 1  # 已匯入不重複
    assert "CreateProfileService;" not in out.split("class CreateProfileServiceTest")[0]  # 同套件不匯入
    assert "Mapper" not in out.split("class CreateProfileServiceTest")[0]
    assert add_missing_project_imports("x.feature", test_src, index) == test_src
    wildcard = "package a.b.other;\nimport a.b.application.port.out.*;\nclass W { SaveProfilePort p; }\n"
    assert add_missing_project_imports("x/src/main/java/a/b/other/W.java", wildcard, index) == wildcard


def test_mockito_extension_tests_become_lenient():
    from java_coding_agent.domain.java_paths import relax_mockito_strictness

    path = "application/src/test/java/a/b/application/usecase/UpdateProfileServiceTest.java"
    src = (
        "package a.b.application.usecase;\n\nimport org.junit.jupiter.api.extension.ExtendWith;\n"
        "import org.mockito.junit.jupiter.MockitoExtension;\n\n/** doc */\n@ExtendWith(MockitoExtension.class)\n"
        "class UpdateProfileServiceTest {\n}\n"
    )
    out = relax_mockito_strictness(path, src)
    assert "@MockitoSettings(strictness = Strictness.LENIENT)\n@ExtendWith(MockitoExtension.class)\nclass" in out
    assert "import org.mockito.junit.jupiter.MockitoSettings;" in out
    assert "import org.mockito.quality.Strictness;" in out
    assert relax_mockito_strictness(path, out) == out
    plain = "package a.b;\nclass T { void t() { var m = org.mockito.Mockito.mock(Object.class); } }\n"
    assert relax_mockito_strictness("domain/src/test/java/a/b/T.java", plain) == plain
    # 模型自己加了 @MockitoSettings 卻沒 import：補 import，不重複加註解
    model_written = (
        "package a.b;\nimport org.junit.jupiter.api.extension.ExtendWith;\n"
        "import org.mockito.junit.jupiter.MockitoExtension;\n"
        "@MockitoSettings(strictness = Strictness.LENIENT)\n@ExtendWith(MockitoExtension.class)\nclass T {}\n"
    )
    out3 = relax_mockito_strictness("domain/src/test/java/a/b/T.java", model_written)
    assert out3.count("@MockitoSettings") == 1
    assert "import org.mockito.junit.jupiter.MockitoSettings;" in out3
    assert "import org.mockito.quality.Strictness;" in out3


def test_generated_value_is_removed_from_persistence_entities():
    from java_coding_agent.domain.java_paths import strip_generated_value

    path = "adapter-persistence/src/main/java/a/b/adapter/persistence/ProfileEntity.java"
    src = (
        "package a.b.adapter.persistence;\n\nimport jakarta.persistence.Entity;\n"
        "import jakarta.persistence.GeneratedValue;\nimport jakarta.persistence.GenerationType;\n"
        "import jakarta.persistence.Id;\n\n@Entity\npublic class ProfileEntity {\n"
        "    @Id\n    @GeneratedValue(strategy = GenerationType.UUID)\n    private UUID id;\n}\n"
    )
    out = strip_generated_value(path, src)
    assert "@GeneratedValue" not in out and "GenerationType" not in out
    assert "    @Id\n    private UUID id;\n" in out
    assert "import jakarta.persistence.Id;" in out
    assert strip_generated_value(path, out) == out
    not_entity = "package a.b.adapter.persistence;\nclass X { @GeneratedValue int y; }\n"
    assert strip_generated_value(path.replace("ProfileEntity", "X"), not_entity) == not_entity
    assert strip_generated_value("domain/src/main/java/a/b/domain/E.java", src) == src


def test_webmvc_tests_get_missing_mockito_beans():
    from java_coding_agent.domain.java_paths import complete_webmvc_mocks, webmvc_controller_name

    controller = (
        "package a.b.adapter.web;\n@RestController\npublic class ProfileController {\n"
        "    private final CreateProfileUseCase create;\n"
        "    ProfileController(CreateProfileUseCase create, GetProfileUseCase get,\n"
        "            UpdateProfileUseCase update, DeleteProfileUseCase delete) {}\n}\n"
    )
    test = (
        "package a.b.adapter.web;\n\nimport org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;\n"
        "import org.springframework.test.context.bean.override.mockito.MockitoBean;\n\n"
        "@WebMvcTest(ProfileController.class)\nclass WebExceptionHandlerTest {\n"
        "    @MockitoBean private CreateProfileUseCase createProfileUseCase;\n"
        "    @MockitoBean private GetProfileUseCase getProfileUseCase;\n}\n"
    )
    assert webmvc_controller_name(test) == "ProfileController"
    out = complete_webmvc_mocks(test, controller)
    assert "private UpdateProfileUseCase updateProfileUseCase;" in out
    assert "private DeleteProfileUseCase deleteProfileUseCase;" in out
    assert out.count("@MockitoBean") == 4
    assert complete_webmvc_mocks(out, controller) == out
    assert webmvc_controller_name("class T {}") is None


def test_statements_after_thrown_assertion_are_removed_in_data_jpa_tests():
    from java_coding_agent.domain.java_paths import truncate_after_thrown_assertion

    path = "adapter-persistence/src/test/java/a/b/adapter/persistence/JpaSaveProfilePortTest.java"
    src = (
        "package a.b.adapter.persistence;\n@DataJpaTest\nclass T {\n"
        "    @Test\n    void dup() {\n        port.save(p1);\n        entityManager.flush();\n"
        "        assertThatThrownBy(() -> port.save(p2))\n"
        "                .isInstanceOf(DuplicateEmailException.class)\n"
        '                .hasMessageContaining("Duplicate; email");\n'
        "        // 不能再查\n        var loaded = entityManager.find(E.class, id1);\n"
        "        assertThat(loaded).isNotNull();\n"
        "    }\n\n    @Test\n    void other() {\n        assertThat(port.findById(id)).isEmpty();\n    }\n}\n"
    )
    out = truncate_after_thrown_assertion(path, src)
    assert "entityManager.find(E.class, id1)" not in out and "assertThat(loaded)" not in out
    assert '.hasMessageContaining("Duplicate; email");' in out
    assert "void other() {\n        assertThat(port.findById(id)).isEmpty();\n    }" in out
    assert "交易已中止" in out
    assert truncate_after_thrown_assertion(path, out) == out
    # 非 DataJpaTest 或非 persistence 測試不處理
    assert truncate_after_thrown_assertion("domain/src/test/java/a/b/T.java", src) == src


def test_gherkin_placeholders_and_bare_uuids_are_rejected():
    from java_coding_agent.domain.java_paths import gherkin_violations

    bad = GeneratedFile(
        "bootstrap/src/test/resources/features/p.feature",
        '# language: zh-TW\n功能: x\n  場景: a\n    當 我以 JSON {string} 呼叫 POST "/profiles"\n'
        "    假設 系統中存在 ID 為 a1b2c3d4-e5f6-7890-abcd-ef1234567890 的個人資料\n"
        '    那麼 回應狀態碼為 201\n  場景大綱: b\n    當 我呼叫 GET "<path>"\n'
        "    例子:\n      | path |\n      | /x |\n",
    )
    good = GeneratedFile(
        "bootstrap/src/test/resources/features/g.feature",
        '功能: y\n  場景: a\n    假設 系統中存在 ID 為 "a1b2c3d4-e5f6-7890-abcd-ef1234567890" 的個人資料\n'
        '    當 我以 JSON 呼叫 POST "/profiles"，內容包含:\n      """json\n      {"id": "{string}"}\n      """\n',
    )
    out = gherkin_violations([bad, good])
    assert len(out) == 2 and "p.feature:4" in out[0] and "p.feature:5" in out[1]
    outline = GeneratedFile(
        "bootstrap/src/test/resources/features/o.feature",
        '功能: z\n  場景大綱: a\n    假設 系統中已存在 ID 為 "{id}" 的個人資料\n    例子:\n      | id |\n      | x |\n',
    )
    assert len(gherkin_violations([outline])) == 1 and "<欄位名>" in gherkin_violations([outline])[0]
    plain_with_var = GeneratedFile(
        "bootstrap/src/test/resources/features/v.feature",
        '功能: w\n  場景: a\n    當 我發送 GET 請求至 "/api/profiles/<id>"\n'
        '  場景大綱: b\n    當 我發送 GET 請求至 "/api/profiles/<id>"\n'
        "    例子:\n      | id |\n      | 1 |\n",
    )
    out3 = gherkin_violations([plain_with_var])
    assert len(out3) == 1 and "v.feature:3" in out3[0] and "場景大綱" in out3[0]


def test_logging_frameworks_are_rejected_in_domain_and_application():
    from java_coding_agent.domain.java_paths import framework_violations

    svc = GeneratedFile(
        "application/src/main/java/a/b/application/usecase/GetProfileService.java",
        "package a.b.application.usecase;\nimport org.slf4j.Logger;\nimport org.slf4j.LoggerFactory;\nclass S {}\n",
    )
    adapter = GeneratedFile(
        "adapter-web/src/main/java/a/b/adapter/web/C.java",
        "package a.b.adapter.web;\nimport org.slf4j.Logger;\nclass C {}\n",
    )
    out = framework_violations([svc, adapter])
    assert len(out) == 1 and out[0].startswith(svc.path) and "org.slf4j.Logger" in out[0]


def test_null_arguments_in_parameterized_providers_are_cast():
    from java_coding_agent.domain.java_paths import rewrite_removed_apis

    src = 'return Stream.of(\n    Arguments.of(null),\n    arguments( null ),\n    Arguments.of("a", null)\n);\n'
    out = rewrite_removed_apis("domain/src/test/java/a/b/T.java", src)
    assert "Arguments.of((Object) null)," in out and "arguments((Object) null)," in out
    assert 'Arguments.of("a", null)' in out  # 多參數時 null 是合法元素，不動
    em = "var f = entityManager.findById(ProfileEntity.class, id);\nvar r = repository.findById(id);\n"
    out_em = rewrite_removed_apis("adapter-persistence/src/test/java/a/b/T.java", em)
    assert "entityManager.find(ProfileEntity.class, id);" in out_em and "repository.findById(id);" in out_em
    assert rewrite_removed_apis("domain/src/main/java/a/b/M.java", src) == src


def test_broken_closing_quote_in_step_annotation_is_repaired():
    from java_coding_agent.domain.java_paths import normalize_cucumber_steps

    src = (
        "import io.cucumber.java.zh_tw.並且;\nclass S {\n"
        '    @並且("隨後嘗試 GET 查詢該 ID 將返回 404\\")\n    void a() {}\n'
        '    @假設("系統中存在 ID 為 \\"<id>\\" 的個人資料\\")\n    void b() {}\n'
        '    @那麼("正常 {string}")\n    void c(String s) {}\n}\n'
    )
    out = normalize_cucumber_steps(src)
    assert '@並且("隨後嘗試 GET 查詢該 ID 將返回 404")' in out
    assert '@假設("系統中存在 ID 為 \\"<id>\\" 的個人資料")' in out
    assert '@那麼("正常 {string}")' in out


def test_missing_jakarta_persistence_imports_are_added():
    from java_coding_agent.domain.java_paths import ensure_jakarta_imports

    path = "adapter-persistence/src/main/java/a/b/adapter/persistence/ProfileEntity.java"
    src = (
        "package a.b.adapter.persistence;\n\nimport jakarta.persistence.Entity;\nimport jakarta.persistence.Id;\n\n"
        '@Entity\n@Table(name = "profiles")\npublic class ProfileEntity {\n    @Id private UUID id;\n'
        "    @Transient private boolean isNew = true;\n    @PostLoad void loaded() { isNew = false; }\n"
        "    @Enumerated(EnumType.STRING) Status s;\n}\n"
    )
    out = ensure_jakarta_imports(path, src)
    for name in ("Table", "Transient", "PostLoad", "Enumerated", "EnumType"):
        assert f"import jakarta.persistence.{name};" in out
    assert out.count("import jakarta.persistence.Id;") == 1
    assert ensure_jakarta_imports(path, out) == out
    plain = "package a.b.domain;\npublic record P() {}\n"
    assert ensure_jakarta_imports("domain/src/main/java/a/b/domain/P.java", plain) == plain


def test_parentheses_in_step_expressions_are_escaped():
    from java_coding_agent.domain.java_paths import normalize_cucumber_steps

    src = (
        "import io.cucumber.java.zh_tw.那麼;\nclass S {\n"
        '    @那麼("回應 ({word}) 的狀態碼應該是 {int}")\n    void a(String w, int c) {}\n'
        '    @當("已跳脫 \\\\(x\\\\)")\n    void b() {}\n}\n'
    )
    out = normalize_cucumber_steps(src)
    assert '@那麼("回應 \\\\({word}\\\\) 的狀態碼應該是 {int}")' in out
    assert '@當("已跳脫 \\\\(x\\\\)")' in out  # 已跳脫不重複


def test_this_assignments_in_record_compact_constructors_are_fixed():
    from java_coding_agent.domain.java_paths import fix_record_compact_constructors

    src = (
        "package a.b.domain;\n\npublic record DisplayName(String name) {\n    public DisplayName {\n"
        "        if (name == null || name.isBlank()) {\n            this.name = null;\n        } else {\n"
        "            this.name = name.trim();\n        }\n    }\n"
        "    public boolean same(DisplayName o) { return this.name.equals(o.name); }\n}\n"
    )
    out = fix_record_compact_constructors("domain/src/main/java/a/b/domain/DisplayName.java", src)
    assert "            name = null;" in out and "            name = name.trim();" in out
    assert "this.name =" not in out
    assert "return this.name.equals(o.name);" in out  # 一般方法不動
    # 精簡建構子裡呼叫 this(...) → 逐一賦值
    with_call = (
        "public record Pair(String a, int b) {\n    public Pair {\n        if (a == null) {\n"
        '            this(normalize(a, ","), b + 1);\n        }\n    }\n}\n'
    )
    out2 = fix_record_compact_constructors("domain/src/main/java/a/b/domain/Pair.java", with_call)
    assert 'a = normalize(a, ","); b = b + 1;' in out2 and "this(" not in out2
    klass = "public class C { private final String n; public C(String n) { this.n = n; } }\n"
    assert fix_record_compact_constructors("domain/src/main/java/a/b/domain/C.java", klass) == klass


def test_response_entity_status_headers_order_is_fixed():
    from java_coding_agent.domain.java_paths import fix_response_entity_arg_order

    src = (
        'this.lastResponse = new ResponseEntity<>(resp.getBody() != null ? resp.getBody() : "", '
        "resp.getStatusCode(), resp.getHeaders());\n"
        "var ok = new ResponseEntity<>(body, headers, HttpStatus.OK);\n"
        "var two = new ResponseEntity<String>(body, HttpStatus.CREATED);\n"
    )
    out = fix_response_entity_arg_order("bootstrap/src/test/java/a/b/bdd/S.java", src)
    assert (
        'new ResponseEntity<>(resp.getBody() != null ? resp.getBody() : "", resp.getHeaders(), resp.getStatusCode());'
        in out
    )
    assert "new ResponseEntity<>(body, headers, HttpStatus.OK);" in out
    assert "new ResponseEntity<String>(body, HttpStatus.CREATED);" in out


def test_status_labels_after_codes_are_removed_on_both_sides():
    from java_coding_agent.domain.java_paths import normalize_cucumber_steps, normalize_gherkin_zh_tw

    feature = (
        "# language: zh-TW\n功能: x\n  場景: a\n    那麼 回應的狀態碼應該是 404 (Not Found)\n"
        "    那麼 回應的狀態碼應該是 201（Created）\n"
    )
    out = normalize_gherkin_zh_tw("f.feature", feature)
    assert "那麼 回應的狀態碼應該是 404\n" in out and "那麼 回應的狀態碼應該是 201\n" in out
    steps = (
        "import io.cucumber.java.zh_tw.那麼;\nclass S {\n"
        '    @那麼("回應的狀態碼應該是 {int} ({word})")\n    void a(int c, String w) {}\n'
        '    @那麼("回應的狀態碼應該是 {int} \\\\(Not Found\\\\)")\n    void b(int c) {}\n'
        '    @那麼("總數為 {int} 筆 (含已刪除)")\n    void c(int n) {}\n}\n'
    )
    out2 = normalize_cucumber_steps(steps)
    assert out2.count('@那麼("回應的狀態碼應該是 {int}")') == 2
    assert '@那麼("總數為 {int} 筆 \\\\(含已刪除\\\\)")' in out2  # 不緊接在 {int} 後的括號只跳脫


def test_duplicate_step_definitions_in_one_class_are_removed():
    from java_coding_agent.domain.java_paths import dedupe_step_definitions

    src = (
        "package a.b.bdd;\nimport io.cucumber.java.zh_tw.那麼;\npublic class S {\n"
        '    @那麼("回應內容中的 email 應該等於 {string}")\n    public void a(String e) {\n'
        "        if (e == null) { fail(); }\n    }\n\n"
        '    @那麼("回應內容中的 email 應該等於 {string}")\n    @Deprecated\n    public void b(String e) {\n'
        "        check(e);\n    }\n\n"
        '    @那麼("其他 {int}")\n    public void c(int n) {}\n}\n'
    )
    out = dedupe_step_definitions("bootstrap/src/test/java/a/b/bdd/S.java", src)
    assert out.count('@那麼("回應內容中的 email 應該等於 {string}")') == 1
    assert "public void a(String e)" in out and "public void b(String e)" not in out and "check(e)" not in out
    assert '@那麼("其他 {int}")' in out and "public void c(int n) {}" in out
    assert dedupe_step_definitions("bootstrap/src/test/java/a/b/bdd/S.java", out) == out


def test_outline_variables_in_plain_scenarios_get_consistent_synthetic_values():
    from java_coding_agent.domain.java_paths import gherkin_violations, normalize_gherkin_zh_tw

    src = (
        "# language: zh-TW\n功能: x\n  場景: 查詢\n"
        '    假設 系統中已存在 ID 為 "<id>"、電子郵件為 "<email>" 的個人資料\n'
        '    當 我發送 GET 請求至 "/api/profiles/<id>"\n    那麼 回應 JSON 的 email 為 "<email>"\n'
        '  場景大綱: 建立\n    當 我以 "<email>" 建立\n    例子:\n      | email |\n      | a@b.c |\n'
    )
    out = normalize_gherkin_zh_tw("f.feature", src)
    plain = out.split("場景大綱")[0]
    assert "<id>" not in plain and "<email>" not in plain
    ids = {m for m in re.findall(r"00000000-0000-4000-8000-\d{12}", plain)}
    assert len(ids) == 1 and plain.count(next(iter(ids))) == 2
    assert plain.count("user") == 2 and "example.com" in plain
    assert '當 我以 "<email>" 建立' in out  # 大綱內保留
    assert gherkin_violations([GeneratedFile("f.feature", out)]) == []


def test_nested_types_shadowing_project_types_are_rejected():
    from java_coding_agent.domain.java_paths import shadowing_violations

    index = {"Profile": "a.b.domain.Profile", "GetProfileUseCase": "a.b.application.port.in.GetProfileUseCase"}
    bad = GeneratedFile(
        "application/src/main/java/a/b/application/port/in/GetProfileUseCase.java",
        "package a.b.application.port.in;\npublic interface GetProfileUseCase {\n"
        "    record Profile(String id) {}\n    Optional<Profile> findById(String id);\n}\n",
    )
    ok = GeneratedFile(
        "application/src/main/java/a/b/application/port/in/CreateProfileUseCase.java",
        "package a.b.application.port.in;\npublic interface CreateProfileUseCase {\n"
        "    record Command(String email) {}\n    Profile execute(Command c);\n}\n",
    )
    out = shadowing_violations([bad, ok], index)
    assert len(out) == 1 and "a.b.domain.Profile" in out[0] and out[0].startswith(bad.path)


def test_adapter_subpackages_are_flattened_with_imports_rewritten():
    text = (
        "=== FILE: adapter-web/src/main/java/a/b/adapter/web/dto/CreateProfileRequest.java ===\n"
        "package a.b.adapter.web.dto;\npublic record CreateProfileRequest(String email) {}\n=== END FILE ===\n"
        "=== FILE: adapter-web/src/main/java/a/b/adapter/web/ProfileController.java ===\n"
        "package a.b.adapter.web;\nimport a.b.adapter.web.dto.CreateProfileRequest;\n"
        "public class ProfileController { CreateProfileRequest r; }\n=== END FILE ===\n"
        "=== FILE: adapter-persistence/src/main/java/a/b/adapter/persistence/converter/ProfileConverter.java ===\n"
        "package a.b.adapter.persistence.converter;\npublic class ProfileConverter {}\n=== END FILE ===\n"
    )
    files = {f.path: f.content for f in parse_file_blocks(text)}
    assert "adapter-web/src/main/java/a/b/adapter/web/CreateProfileRequest.java" in files
    assert "package a.b.adapter.web;" in files["adapter-web/src/main/java/a/b/adapter/web/CreateProfileRequest.java"]
    ctrl = files["adapter-web/src/main/java/a/b/adapter/web/ProfileController.java"]
    assert "import a.b.adapter.web.CreateProfileRequest;" in ctrl and ".dto." not in ctrl
    assert "adapter-persistence/src/main/java/a/b/adapter/persistence/ProfileConverter.java" in files
    assert not any("/dto/" in p or "/converter/" in p for p in files)


def test_request_body_parameters_get_valid_annotation():
    from java_coding_agent.domain.java_paths import ensure_valid_on_request_body

    path = "adapter-web/src/main/java/a/b/adapter/web/ProfileController.java"
    src = (
        "package a.b.adapter.web;\n\nimport org.springframework.web.bind.annotation.*;\n\n@RestController\n"
        "public class ProfileController {\n    @PostMapping\n"
        "    public String create(@RequestBody CreateProfileRequest r) { return r.email(); }\n"
        "    @PutMapping\n"
        "    public String update(@PathVariable String id, "
        "@Valid @RequestBody UpdateProfileRequest r) { return id; }\n}\n"
    )
    out = ensure_valid_on_request_body(path, src)
    assert "create(@Valid @RequestBody CreateProfileRequest r)" in out
    assert out.count("@Valid @RequestBody") == 2 and "@Valid @Valid" not in out
    assert "import jakarta.validation.Valid;" in out
    assert ensure_valid_on_request_body(path, out) == out
    assert ensure_valid_on_request_body("adapter-web/src/test/java/a/b/adapter/web/T.java", src) == src


def test_cucumber_step_classes_are_made_public():
    from java_coding_agent.domain.java_paths import publicize_cucumber_steps

    src = (
        "package a.b.bdd;\n\nimport io.cucumber.java.zh_tw.當;\n\n"
        'class PersonalProfileSteps {\n    @當("x")\n    void a() {}\n}\n'
    )
    out = publicize_cucumber_steps("bootstrap/src/test/java/a/b/bdd/PersonalProfileSteps.java", src)
    assert "\npublic class PersonalProfileSteps {" in out
    assert '    @當("x")\n    public void a() {}' in out  # 步驟方法也必須 public，否則 Class.getMethods() 看不到
    assert publicize_cucumber_steps("bootstrap/src/test/java/a/b/bdd/PersonalProfileSteps.java", out) == out
    methods = (
        "package a.b.bdd;\nimport io.cucumber.java.zh_tw.當;\npublic class S {\n"
        '    @當("我發送 POST 請求至 {string}，內容包含:")\n'
        "    void sendPostRequest(String path, String content) throws Exception {}\n"
        '    @當("y")\n    @Transactional\n    Map<String, Object> read(String p) { return null; }\n'
        '    @當("z")\n    public void ok() {}\n'
        "    private void helper() {}\n}\n"
    )
    fixed = publicize_cucumber_steps("bootstrap/src/test/java/a/b/bdd/S.java", methods)
    assert "    public void sendPostRequest(String path, String content) throws Exception {}" in fixed
    assert "    public Map<String, Object> read(String p)" in fixed
    assert fixed.count("public void ok()") == 1 and "    private void helper() {}" in fixed
    assert publicize_cucumber_steps("bootstrap/src/test/java/a/b/bdd/S.java", fixed) == fixed
    plain = "package a.b;\nclass T {}\n"
    assert publicize_cucumber_steps("domain/src/test/java/a/b/T.java", plain) == plain


def test_mock_fields_without_extension_get_mockito_extension():
    from java_coding_agent.domain.java_paths import ensure_mockito_extension

    path = "application/src/test/java/a/b/application/usecase/UpdateProfileServiceTest.java"
    src = (
        "package a.b.application.usecase;\n\nimport org.junit.jupiter.api.Test;\nimport org.mockito.Mock;\n\n"
        "class UpdateProfileServiceTest {\n    @Mock private LoadProfilePort loadProfilePort;\n"
        "    @Test void t() {}\n}\n"
    )
    out = ensure_mockito_extension(path, src)
    assert "@ExtendWith(MockitoExtension.class)\nclass UpdateProfileServiceTest {" in out
    assert "import org.junit.jupiter.api.extension.ExtendWith;" in out
    assert "import org.mockito.junit.jupiter.MockitoExtension;" in out
    assert ensure_mockito_extension(path, out) == out
    spring = "package a.b;\n@WebMvcTest(C.class)\nclass T {\n    @MockitoBean X x;\n    @Mock Y y;\n}\n"
    assert ensure_mockito_extension("adapter-web/src/test/java/a/b/T.java", spring) == spring
    manual = "package a.b;\nclass T {\n    @Mock X x;\n    void setUp() { MockitoAnnotations.openMocks(this); }\n}\n"
    assert ensure_mockito_extension("domain/src/test/java/a/b/T.java", manual) == manual


def test_repository_interfaces_are_removed_from_data_jpa_test_imports():
    from java_coding_agent.domain.java_paths import normalize_data_jpa_tests

    path = "adapter-persistence/src/test/java/a/b/adapter/persistence/JpaSaveProfilePortTest.java"
    src = (
        "package a.b.adapter.persistence;\n@DataJpaTest\n"
        "@Import({JpaSaveProfilePort.class, ProfileRepository.class, ProfileConverter.class})\n"
        "class JpaSaveProfilePortTest {\n    @Autowired private jakarta.persistence.EntityManager em;\n}\n"
    )
    out = normalize_data_jpa_tests(path, src)
    assert "@Import({JpaSaveProfilePort.class, ProfileConverter.class})" in out
    assert "ProfileRepository.class" not in out
    only_repo = src.replace(
        "{JpaSaveProfilePort.class, ProfileRepository.class, ProfileConverter.class}", "ProfileJpaRepository.class"
    )
    out2 = normalize_data_jpa_tests(path, only_repo)
    assert "@Import" not in out2


def test_uniqueness_test_with_different_emails_is_rejected():
    from java_coding_agent.domain.java_paths import uniqueness_test_violations

    bad = GeneratedFile(
        "adapter-persistence/src/test/java/a/b/adapter/persistence/JpaSaveProfilePortTest.java",
        "class JpaSaveProfilePortTest {\n"
        "    @Test\n"
        "    void should_update_existing_profile() {\n"
        '        var p = Profile.create(new ProfileId(id), new Email("updated@example.com"), new DisplayName("x"));\n'
        "    }\n\n"
        "    @Test\n"
        "    void should_throw_duplicate_email_exception_on_conflict() {\n"
        "        saveProfilePort.save(createValidProfile(id1));\n"
        '        var profile2 = createValidProfileWithSameEmail(id2, "dup@test.com");\n'
        "        assertThatThrownBy(() -> saveProfilePort.save(profile2))\n"
        "            .isInstanceOf(DuplicateEmailException.class);\n"
        "    }\n\n"
        "    private Profile createValidProfile(String id) {\n"
        '        return Profile.create(new ProfileId(id), new Email("test@example.com"), new DisplayName("T"));\n'
        "    }\n\n"
        "    private Profile createValidProfileWithSameEmail(String id, String emailAddr) {\n"
        '        return Profile.create(new ProfileId(id), new Email(emailAddr), new DisplayName("D"));\n'
        "    }\n"
        "}\n",
    )
    problems = uniqueness_test_violations([bad])
    assert len(problems) == 1
    assert "should_throw_duplicate_email_exception_on_conflict()" in problems[0]
    assert "dup@test.com" in problems[0] and "test@example.com" in problems[0]
    assert "updated@example.com" not in problems[0]  # 其他測試方法的 email 不算

    good = GeneratedFile(
        bad.path,
        "class JpaSaveProfilePortTest {\n"
        "    @Test\n"
        "    void should_reject_duplicate_email() {\n"
        '        String email = "dup@example.com";\n'
        "        saveProfilePort.save(profile(id1, email));\n"
        "        assertThatThrownBy(() -> saveProfilePort.save(profile(id2, email)))\n"
        "            .isInstanceOf(DuplicateEmailException.class);\n"
        "    }\n"
        "    private Profile profile(String id, String email) { return Profile.create(id, new Email(email)); }\n"
        "}\n",
    )
    main_file = GeneratedFile("domain/src/main/java/a/b/domain/X.java", bad.content)
    assert uniqueness_test_violations([good, main_file]) == []


def test_uniqueness_check_ignores_mockito_tests_and_non_persistence_modules():
    from java_coding_agent.domain.java_paths import uniqueness_test_violations

    body = (
        "class UpdateProfileServiceTest {\n"
        "    @Test\n"
        "    void should_propagate_duplicate_email_exception_from_save_port() {\n"
        '        var e = Profile.create(new ProfileId(id), new Email("old@example.com"), new DisplayName("a"));\n'
        "        when(loadPort.findById(id)).thenReturn(Optional.of(existing));\n"
        '        doThrow(new DuplicateEmailException("dup")).when(savePort).save(any());\n'
        '        assertThatThrownBy(() -> service.execute(id, new UpdateProfileCommand("new@example.com", "b")))\n'
        "            .isInstanceOf(DuplicateEmailException.class);\n"
        "    }\n"
        "}\n"
    )
    app = GeneratedFile("application/src/test/java/a/b/application/usecase/UpdateProfileServiceTest.java", body)
    mocked_persistence = GeneratedFile("adapter-persistence/src/test/java/a/b/adapter/persistence/XTest.java", body)
    assert uniqueness_test_violations([app, mocked_persistence]) == []


def test_gherkin_lines_starting_with_qie_become_and_steps_and_match_step_definitions():
    from java_coding_agent.domain.java_paths import normalize_cucumber_steps, normalize_gherkin_zh_tw

    feature = (
        "# language: zh-TW\n功能: x\n  場景: y\n"
        '    假設系統中存在 ID 為 "a" 的個人資料\n'
        '    且其電子郵件為 "x"，顯示名稱為 "y"\n'
        "    但是回應的狀態碼應為 404\n"
    )
    out = normalize_gherkin_zh_tw("f.feature", feature)
    assert '    並且其電子郵件為 "x"，顯示名稱為 "y"\n' in out
    assert "個人資料，且其" not in out  # 不再被當成接續行折回上一步
    assert "但是回應的狀態碜應為" not in out and "    但是回應的狀態碼應為 404\n" in out  # 但是 不可變成 但是是
    assert normalize_gherkin_zh_tw("f.feature", out) == out

    steps = (
        "package a.b.bdd;\nimport io.cucumber.java.zh_tw.並且;\npublic class S {\n"
        '    @並且("且其電子郵件為 {string}，顯示名稱為 {string}")\n    public void a(String e, String n) {}\n'
        '    @並且("且目前顯示名稱為 {string}")\n    public void b(String n) {}\n}\n'
    )
    fixed = normalize_cucumber_steps(steps)
    assert '@並且("其電子郵件為 {string}，顯示名稱為 {string}")' in fixed
    assert '@並且("目前顯示名稱為 {string}")' in fixed
    assert normalize_cucumber_steps(fixed) == fixed


def test_save_port_without_lookup_is_rejected():
    from java_coding_agent.domain.java_paths import persistence_violations

    base = "adapter-persistence/src/main/java/a/b/adapter/persistence/"
    insert_only = GeneratedFile(
        base + "JpaSaveProfilePort.java",
        "public class JpaSaveProfilePort implements SaveProfilePort {\n"
        "    public Profile save(Profile p) { return toDomain(repository.saveAndFlush(toEntity(p))); }\n}\n",
    )
    upsert = GeneratedFile(
        base + "JpaSaveProfilePort.java",
        "public class JpaSaveProfilePort implements SaveProfilePort {\n"
        "    public Profile save(Profile p) {\n"
        "        var e = repository.findById(p.id().value()).orElseGet(ProfileEntity::new);\n"
        "        e.setEmail(p.email().value()); return toDomain(repository.saveAndFlush(e));\n    }\n}\n",
    )
    load_port = GeneratedFile(
        base + "JpaLoadProfilePort.java",
        "public class JpaLoadProfilePort implements LoadProfilePort {\n"
        "    public Optional<Profile> load(ProfileId id) {\n"
        "        return repository.findById(id.value()).map(this::toDomain);\n    }\n}\n",
    )
    out = persistence_violations([insert_only])
    assert len(out) == 1 and "findById" in out[0]
    assert persistence_violations([upsert, load_port]) == []


def test_cause_assertions_are_stripped_from_tests():
    from java_coding_agent.domain.java_paths import strip_cause_assertions

    src = (
        "        assertThatThrownBy(() -> port.save(dup))\n"
        "            .isInstanceOf(DuplicateEmailException.class)\n"
        "            .hasCauseInstanceOf(DataIntegrityViolationException.class)\n"
        '            .hasMessageContaining("email");\n'
        "        assertThatThrownBy(() -> x()).hasRootCauseInstanceOf(SQLException.class);\n"
    )
    out = strip_cause_assertions("adapter-persistence/src/test/java/a/b/T.java", src)
    assert "hasCauseInstanceOf" not in out and "hasRootCauseInstanceOf" not in out
    assert (
        '            .isInstanceOf(DuplicateEmailException.class)\n            .hasMessageContaining("email");' in out
    )
    assert "assertThatThrownBy(() -> x());" in out
    assert strip_cause_assertions("adapter-persistence/src/main/java/a/b/P.java", src) == src


def test_http_headers_contains_key_is_rewritten_for_spring_7():
    from java_coding_agent.domain.java_paths import rewrite_removed_apis

    src = (
        'assertThat(response.getHeaders().containsKey("Location")).isTrue();\n'
        'if (headers.containsKey("X")) {}\n'
        'map.containsKey("k");\n'
    )
    out = rewrite_removed_apis("bootstrap/src/test/java/a/b/bdd/S.java", src)
    assert 'response.getHeaders().containsHeader("Location")' in out
    assert 'headers.containsHeader("X")' in out
    assert 'map.containsKey("k")' in out


def test_missing_zh_tw_step_annotation_imports_are_added():
    from java_coding_agent.domain.java_paths import normalize_cucumber_steps

    src = (
        "package a.b.bdd;\n\nimport io.cucumber.java.zh_tw.當;\nimport io.cucumber.java.zh_tw.那麼;\n\n"
        'public class S {\n    @當("a")\n    public void a() {}\n'
        '    @那麼("b")\n    public void b() {}\n'
        '    @並且("c")\n    public void c() {}\n'
        '    @假設("d")\n    public void d() {}\n}\n'
    )
    out = normalize_cucumber_steps(src)
    assert "import io.cucumber.java.zh_tw.並且;\n" in out and "import io.cucumber.java.zh_tw.假設;\n" in out
    assert out.count("import io.cucumber.java.zh_tw.當;") == 1
    assert normalize_cucumber_steps(out) == out


def test_symbolic_ids_in_feature_become_consistent_uuids():
    from java_coding_agent.domain.java_paths import normalize_gherkin_zh_tw

    feature = (
        "# language: zh-TW\n功能: x\n  場景: 更新\n"
        '    假設系統中已存在 ID 為 "update-id" 的個人資料\n'
        '    當我發送 PUT 請求至 "/api/profiles/update-id"，內容包含:\n'
        "    那麼回應的狀態碼應該是 200\n"
        "  場景: 不存在\n"
        '    當我發送 GET 請求至 "/api/profiles/non-existent-id"\n'
        "    那麼回應的狀態碼應該是 404\n"
    )
    out = normalize_gherkin_zh_tw("f.feature", feature)
    assert "update-id" not in out and "non-existent-id" not in out
    first = "00000000-0000-4000-8000-000000009001"
    assert f'ID 為 "{first}" 的個人資料' in out and f'"/api/profiles/{first}"' in out
    assert '"/api/profiles/00000000-0000-4000-8000-000000009002"' in out
    assert normalize_gherkin_zh_tw("f.feature", out) == out


def test_inject_mocks_field_initializers_are_removed():
    from java_coding_agent.domain.java_paths import fix_inject_mocks_initializers

    src = (
        "    @Mock\n    private FindProfilePort findProfilePort;\n\n"
        "    @InjectMocks\n    private GetProfileUseCase getProfileUseCase = new GetProfileService(findProfilePort);\n"
        "    @InjectMocks private final CreateProfileService service = new CreateProfileService(a, b);\n"
        "    @InjectMocks\n    private DeleteProfileService deleteService;\n"
    )
    out = fix_inject_mocks_initializers("application/src/test/java/a/b/T.java", src)
    assert "    @InjectMocks\n    private GetProfileService getProfileUseCase;\n" in out
    assert "    @InjectMocks private CreateProfileService service;\n" in out
    assert "    @InjectMocks\n    private DeleteProfileService deleteService;\n" in out
    assert fix_inject_mocks_initializers("application/src/test/java/a/b/T.java", out) == out
    assert fix_inject_mocks_initializers("application/src/main/java/a/b/T.java", src) == src


def test_plain_field_initializers_using_mocks_become_inject_mocks():
    from java_coding_agent.domain.java_paths import fix_inject_mocks_initializers

    src = (
        "package a.b;\n\nimport org.mockito.Mock;\n\nclass CreateProfileServiceTest {\n"
        "    @Mock\n    private SaveProfilePort saveProfilePort;\n"
        "    @Mock private LoadProfilePort loadProfilePort;\n"
        "    private final CreateProfileService service = new CreateProfileService(saveProfilePort, loadProfilePort);\n"
        "    private final ObjectMapper mapper = new ObjectMapper();\n"
        '    private final Email email = new Email("a@b.c");\n'
        "}\n"
    )
    out = fix_inject_mocks_initializers("application/src/test/java/a/b/CreateProfileServiceTest.java", src)
    assert "    @InjectMocks\n    private CreateProfileService service;\n" in out
    assert "new ObjectMapper();" in out and 'new Email("a@b.c");' in out  # 不含 mock 的初始化不動
    assert "import org.mockito.InjectMocks;" in out
    assert fix_inject_mocks_initializers("application/src/test/java/a/b/CreateProfileServiceTest.java", out) == out


def test_scenario_with_examples_table_is_promoted_to_outline_and_cjk_vars_substituted():
    from java_coding_agent.domain.java_paths import gherkin_violations, normalize_gherkin_zh_tw

    feature = (
        "# language: zh-TW\n功能: x\n"
        "    場景: 成功查詢存在的個人資料\n"
        '        假設系統中已存在 ID 為 "<id>" 的個人資料\n'
        '        並且回應主體中的姓名應為 "<姓名>"\n'
        "        例子:\n"
        "            | id | 姓名 |\n"
        "            | 550e8400-e29b-41d4-a716-446655440000 | 王小明 |\n"
        "    場景: 查詢不存在的個人資料\n"
        '        當我發送 GET 請求至 "/profiles/<id>"\n'
        '        並且電子郵件為 "<電子郵件>"\n'
        "        那麼回應的狀態碼應為 404\n"
    )
    out = normalize_gherkin_zh_tw("f.feature", feature)
    assert "    場景大綱: 成功查詢存在的個人資料\n" in out
    assert '假設系統中已存在 ID 為 "<id>" 的個人資料' in out  # 大綱內的變數保留
    assert "    場景: 查詢不存在的個人資料\n" in out
    assert "<id>" not in out.split("場景: 查詢不存在")[1] and "<電子郵件>" not in out
    assert "@example.com" in out
    assert gherkin_violations([GeneratedFile("bootstrap/src/test/resources/features/f.feature", out)]) == []
    assert normalize_gherkin_zh_tw("f.feature", out) == out


def test_uuid_inside_quoted_path_is_not_a_violation():
    from java_coding_agent.domain.java_paths import gherkin_violations

    ok = GeneratedFile(
        "bootstrap/src/test/resources/features/f.feature",
        '功能: x\n  場景: y\n    當我發送 GET 請求至 "/profiles/550e8400-e29b-41d4-a716-446655440000/avatar"\n',
    )
    bad = GeneratedFile(
        "bootstrap/src/test/resources/features/g.feature",
        "功能: x\n  場景: y\n    當我發送 GET 請求至 /profiles/550e8400-e29b-41d4-a716-446655440000\n",
    )
    assert gherkin_violations([ok]) == []
    assert len(gherkin_violations([bad])) == 1


def test_trailing_parenthetical_notes_are_stripped_from_feature_steps():
    from java_coding_agent.domain.java_paths import normalize_gherkin_zh_tw

    feature = (
        "# language: zh-TW\n功能: x\n  場景: y\n"
        '    當我發送 GET 請求至 "/profiles/99999"（假設該 ID 不存在）\n'
        "    那麼回應的狀態碼應為 404 (Not Found)\n"
        '    並且回應內容包含 "錯誤（不存在）"\n'
    )
    out = normalize_gherkin_zh_tw("f.feature", feature)
    assert (
        '    當我發送 GET 請求至 "/profiles/00000000-0000-4000-8000-000000009001"\n' in out
    )  # 括號說明移除、數字 id 轉 UUID
    assert "    那麼回應的狀態碼應為 404\n" in out
    assert '並且回應內容包含 "錯誤（不存在）"' in out  # 引號內的括號是參數的一部分，不動
    assert normalize_gherkin_zh_tw("f.feature", out) == out


def test_numeric_ids_in_feature_become_uuids_too():
    from java_coding_agent.domain.java_paths import normalize_gherkin_zh_tw

    feature = (
        "# language: zh-TW\n功能: x\n  場景: y\n"
        '    假設系統中已存在 ID 為 "99999" 的個人資料\n'
        '    當我發送 GET 請求至 "/profiles/99999"\n'
        "    那麼回應的狀態碼應為 404\n"
    )
    out = normalize_gherkin_zh_tw("f.feature", feature)
    uuid = "00000000-0000-4000-8000-000000009001"
    assert f'ID 為 "{uuid}" 的個人資料' in out and f'"/profiles/{uuid}"' in out
    assert "那麼回應的狀態碼應為 404" in out  # 狀態碼不是 id，不動
    assert normalize_gherkin_zh_tw("f.feature", out) == out


def test_fixed_id_precondition_via_post_is_rejected():
    from java_coding_agent.domain.java_paths import cucumber_precondition_violations

    path = "bootstrap/src/test/java/a/b/bdd/PersonalProfileSteps.java"
    bad = GeneratedFile(
        path,
        "package a.b.bdd;\nimport io.cucumber.java.zh_tw.假設;\npublic class PersonalProfileSteps {\n"
        '    @假設("已有一個 ID 為 {string} 的個人資料")\n'
        "    public void existingProfileWithId(String id) {\n"
        '        seedProfile(id, "seed@example.com", "Seed User");\n    }\n'
        "    private void seedProfile(String idStr, String email, String name) {\n"
        '        restTemplate.postForEntity("/api/profiles", "{}", String.class);\n    }\n}\n',
    )
    good = GeneratedFile(
        path,
        "package a.b.bdd;\nimport io.cucumber.java.zh_tw.假設;\npublic class PersonalProfileSteps {\n"
        "    @Autowired private SaveProfilePort saveProfilePort;\n"
        '    @假設("已有一個 ID 為 {string} 的個人資料")\n'
        "    public void existingProfileWithId(String id) {\n"
        '        saveProfilePort.save(Profile.create(new ProfileId(id), new Email("a@b.c"), new DisplayName("x")));\n'
        "    }\n"
        '    @假設("已經存在一個電子郵件為 {string} 的個人資料")\n'
        '    public void byEmail(String email) { restTemplate.postForEntity("/api/profiles", "{}", String.class); }\n'
        "}\n",
    )
    problems = cucumber_precondition_violations([bad])
    assert len(problems) == 1 and "existingProfileWithId()" in problems[0]
    assert cucumber_precondition_violations([good]) == []  # 以 email 為前置、不指定 id 的 POST 是可以的


def test_step_class_without_any_cucumber_import_is_still_normalized():
    text = (
        "=== FILE: bootstrap/src/test/java/a/b/bdd/PersonalProfileSteps.java ===\n"
        "package a.b.bdd;\n\nimport io.cucumber.datatable.DataTable;\n\n"
        "class PersonalProfileSteps {\n"
        '    @當("我發送 POST 請求至 {string}，內容包含:")\n'
        "    void post(String path, String body) {}\n"
        '    @那麼("回應的狀態碼應該是 {int}")\n'
        "    void status(int s) {}\n}\n"
        "=== END FILE ===\n"
    )
    out = parse_file_blocks(text)[0].content
    assert "import io.cucumber.java.zh_tw.當;" in out and "import io.cucumber.java.zh_tw.那麼;" in out
    assert "public class PersonalProfileSteps" in out
    assert "    public void post(String path, String body) {}" in out


def test_emails_reused_across_scenarios_are_made_unique_but_consistent_within_a_scenario():
    from java_coding_agent.domain.java_paths import normalize_gherkin_zh_tw

    feature = (
        "# language: zh-TW\n功能: x\n"
        "  場景: 建立\n"
        '    當我發送 POST 請求至 "/api/profiles"，內容包含:\n'
        '      """json\n      {"email": "user@example.com"}\n      """\n'
        "    那麼回應的狀態碼應該是 201\n"
        "  場景: 重複\n"
        '    假設已存在一個電子郵件為 "dup@test.com" 的個人資料\n'
        '    當我發送 POST 請求至 "/api/profiles"，內容包含:\n'
        '      """json\n      {"email": "dup@test.com"}\n      """\n'
        "    那麼回應的狀態碼應該是 409\n"
        "  場景: 更新\n"
        '    當我發送 PUT 請求至 "/api/profiles/550e8400-e29b-41d4-a716-446655440000"，內容包含:\n'
        '      """json\n      {"email": "user@example.com"}\n      """\n'
        '    並且回應內容中的 email 應該等於 "user@example.com"\n'
    )
    out = normalize_gherkin_zh_tw("f.feature", feature)
    first, second, third = out.split("  場景: ")[1:]
    assert first.count("user@example.com") == 1
    assert second.count("dup@test.com") == 2  # 同場景內的重複 email 保留（那正是測試目的）
    assert '"user@example.com"' not in third and third.count("s3.user@example.com") == 2
    assert normalize_gherkin_zh_tw("f.feature", out) == out
