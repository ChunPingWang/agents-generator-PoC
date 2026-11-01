from java_coding_agent.domain.java_naming import base_package_for, package_path, project_name_for
from java_coding_agent.domain.models import RepoSettings


def test_base_package_is_lowercase_alnum():
    repo = RepoSettings(url="https://github.com/ChunPingWang/ollama-agent-outcome.git")
    assert base_package_for(repo) == "com.github.chunpingwang.ollamaagentoutcome"
    assert project_name_for(repo) == "ollama-agent-outcome"
    assert package_path("a.b.c") == "a/b/c"


def test_segment_starting_with_digit_gets_prefix():
    assert base_package_for(RepoSettings(url="https://github.com/9lives/1app")) == "com.github.x9lives.x1app"


def test_package_violations_detects_wrong_module_package():
    from java_coding_agent.domain.java_paths import package_violations
    from java_coding_agent.domain.models import GeneratedFile

    base = "com.acme.app"
    ok = [
        GeneratedFile("domain/src/main/java/com/acme/app/domain/Profile.java", "package com.acme.app.domain;"),
        GeneratedFile(
            "adapter-persistence/src/main/java/com/acme/app/adapter/persistence/E.java",
            "package com.acme.app.adapter.persistence;",
        ),
        GeneratedFile("bootstrap/src/main/java/com/acme/app/Application.java", "package com.acme.app;"),
        GeneratedFile("bootstrap/src/test/java/com/acme/app/bdd/Steps.java", "package com.acme.app.bdd;"),
        GeneratedFile("bootstrap/src/test/resources/features/x.feature", "功能: x"),
        GeneratedFile("specs/001-x/spec.md", "# spec"),
    ]
    assert package_violations(ok, base) == []
    bad = [
        GeneratedFile(
            "adapter-persistence/src/main/java/com/acme/app/persistence/E.java", "package com.acme.app.persistence;"
        ),
        GeneratedFile("domain/src/main/java/com/acme/app/domain/NoPkg.java", "class NoPkg {}"),
        GeneratedFile("infra/src/main/java/com/acme/app/X.java", "package com.acme.app;"),
    ]
    problems = package_violations(bad, base)
    assert len(problems) == 3
    assert "com.acme.app.persistence" in problems[0] and "adapter-persistence" in problems[0]
    assert "缺少 package" in problems[1]
    assert "未知的模組目錄" in problems[2]


def test_duplicate_simple_names_within_module():
    from java_coding_agent.domain.java_paths import duplicate_simple_names

    paths = [
        "application/src/main/java/a/b/application/port/in/DeleteProfilePort.java",
        "application/src/main/java/a/b/application/port/out/DeleteProfilePort.java",
        "application/src/main/java/a/b/application/port/out/SaveProfilePort.java",
        "adapter-persistence/src/main/java/a/b/adapter/persistence/SaveProfilePort.java",  # 不同模組，允許
        "application/src/test/java/a/b/application/DeleteProfilePort.java",  # 測試不計
    ]
    problems = duplicate_simple_names(paths)
    assert len(problems) == 1 and "DeleteProfilePort" in problems[0] and "application" in problems[0]


def test_framework_violations_in_domain_and_application_only():
    from java_coding_agent.domain.java_paths import framework_violations
    from java_coding_agent.domain.models import GeneratedFile

    files = [
        GeneratedFile(
            "application/src/main/java/a/b/application/usecase/S.java",
            "package a.b.application.usecase;\nimport org.springframework.stereotype.Service;\n@Service class S {}",
        ),
        GeneratedFile(
            "domain/src/main/java/a/b/domain/E.java", "package a.b.domain;\nimport jakarta.persistence.Entity;"
        ),
        GeneratedFile(
            "adapter-persistence/src/main/java/a/b/adapter/persistence/P.java",
            "package a.b.adapter.persistence;\nimport jakarta.persistence.Entity;\n@Entity class P {}",
        ),
        GeneratedFile(
            "application/src/test/java/a/b/application/T.java",
            "package a.b.application;\nimport org.springframework.x.Y;",
        ),
    ]
    problems = framework_violations(files)
    assert len(problems) == 2 and "@Service" in problems[0] and "domain" in problems[1]


def test_path_variable_violations():
    from java_coding_agent.domain.java_paths import path_variable_violations
    from java_coding_agent.domain.models import GeneratedFile

    bad = GeneratedFile(
        "adapter-web/src/main/java/a/b/adapter/web/C.java",
        """@RestController
class C {
    @DeleteMapping("/{id}")
    ResponseEntity<Void> delete(@PathVariable String profileId) { return null; }

    @GetMapping("/{id}")
    ResponseEntity<String> get(@PathVariable("id") String profileId) { return null; }

    @PutMapping("/{id}")
    ResponseEntity<String> put(@PathVariable String id, @Valid @RequestBody Req r) { return null; }
}""",
    )
    problems = path_variable_violations([bad])
    assert len(problems) == 1 and "profileId" in problems[0] and '@PathVariable("id")' in problems[0]


def test_truncated_java_files_detects_unbalanced_braces():
    from java_coding_agent.domain.java_paths import truncated_java_files
    from java_coding_agent.domain.models import GeneratedFile

    ok = GeneratedFile("a/A.java", 'class A { String s = "{"; /* } */ void f() { } }')
    cut = GeneratedFile("a/B.java", "class B {\n    void f() {\n")
    text = GeneratedFile("x.feature", "功能: {")
    problems = truncated_java_files([ok, cut, text])
    assert len(problems) == 1 and "a/B.java" in problems[0] and "截斷" in problems[0]


def test_test_libraries_in_main_sources_are_rejected():
    from java_coding_agent.domain.java_paths import framework_violations
    from java_coding_agent.domain.models import GeneratedFile

    bad = GeneratedFile(
        "bootstrap/src/main/java/a/b/bootstrap/TestcontainersConfiguration.java",
        "package a.b.bootstrap;\nimport org.testcontainers.postgresql.PostgreSQLContainer;\nclass T {}",
    )
    ok = GeneratedFile(
        "bootstrap/src/test/java/a/b/bootstrap/X.java",
        "package a.b.bootstrap;\nimport org.testcontainers.postgresql.PostgreSQLContainer;\nclass X {}",
    )
    problems = framework_violations([bad, ok])
    assert len(problems) == 1 and "src/main 不得 import 測試用套件" in problems[0]


def test_adapter_subpackages_are_rejected():
    from java_coding_agent.domain.java_paths import package_violations
    from java_coding_agent.domain.models import GeneratedFile

    base = "com.acme.app"
    files = [
        GeneratedFile(
            "adapter-persistence/src/main/java/com/acme/app/adapter/persistence/port/out/P.java",
            "package com.acme.app.adapter.persistence.port.out;",
        ),
        GeneratedFile(
            "adapter-web/src/main/java/com/acme/app/adapter/web/dto/D.java", "package com.acme.app.adapter.web.dto;"
        ),
        GeneratedFile("adapter-web/src/main/java/com/acme/app/adapter/web/C.java", "package com.acme.app.adapter.web;"),
        GeneratedFile(
            "application/src/main/java/com/acme/app/application/port/in/U.java",
            "package com.acme.app.application.port.in;",
        ),
    ]
    problems = package_violations(files, base)
    assert len(problems) == 2 and all("不得使用子套件" in p for p in problems)


def test_domain_and_application_subpackage_rules():
    from java_coding_agent.domain.java_paths import package_violations
    from java_coding_agent.domain.models import GeneratedFile

    base = "com.acme.app"
    files = [
        GeneratedFile("domain/src/main/java/com/acme/app/domain/model/P.java", "package com.acme.app.domain.model;"),
        GeneratedFile("domain/src/main/java/com/acme/app/domain/P2.java", "package com.acme.app.domain;"),
        GeneratedFile(
            "application/src/main/java/com/acme/app/application/command/C.java",
            "package com.acme.app.application.command;",
        ),
        GeneratedFile(
            "application/src/main/java/com/acme/app/application/usecase/S.java",
            "package com.acme.app.application.usecase;",
        ),
        GeneratedFile(
            "application/src/main/java/com/acme/app/application/port/out/O.java",
            "package com.acme.app.application.port.out;",
        ),
    ]
    problems = package_violations(files, base)
    assert len(problems) == 2
    assert "domain" in problems[0] and "application 只允許" in problems[1]


def test_cucumber_custom_parameter_types_are_rejected():
    from java_coding_agent.domain.java_paths import cucumber_step_violations
    from java_coding_agent.domain.models import GeneratedFile

    steps = GeneratedFile(
        "bootstrap/src/test/java/a/b/bdd/S.java",
        'import io.cucumber.java.zh_tw.假設;\n@假設("已存在一筆 ID 為 {uuid} 的資料")\nvoid a(java.util.UUID id) {}\n'
        '@當("我發送 GET 請求至 {string}")\nvoid b(String p) {}\n'
        '@那麼("狀態碼為 {int} {word}")\nvoid c(int s, String w) {}\n',
    )
    problems = cucumber_step_violations([steps])
    assert len(problems) == 1 and "{uuid}" in problems[0]


def test_unavailable_libraries_are_rejected_in_any_module():
    from java_coding_agent.domain.java_paths import framework_violations
    from java_coding_agent.domain.models import GeneratedFile

    files = [
        GeneratedFile(
            "application/src/main/java/a/b/application/usecase/S.java",
            "package a.b.application.usecase;\nimport static org.apache.commons.lang3.StringUtils.isBlank;\nclass S {}",
        ),
        GeneratedFile(
            "adapter-web/src/main/java/a/b/adapter/web/C.java",
            "package a.b.adapter.web;\nimport lombok.Data;\nclass C {}",
        ),
        GeneratedFile(
            "adapter-web/src/main/java/a/b/adapter/web/D.java",
            "package a.b.adapter.web;\nimport java.util.Objects;\nclass D {}",
        ),
    ]
    problems = framework_violations(files)
    assert len(problems) == 2 and "org.apache.commons" in problems[0] and "lombok" in problems[1]
