"""依 Java 檔案的 package 宣告校正輸出路徑。

LLM 偶爾會把套件目錄寫錯（例如 `com/github/owner.repo/...` 以點取代斜線），導致同名類別重複。
規則：若檔案為 .java 且含 `package a.b.c;`，則路徑的 `src/{main,test}/java/` 之後必須是 `a/b/c/<FileName>`。
"""

from __future__ import annotations

import functools
import re
from collections.abc import Mapping

_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
_SRC_ROOT = re.compile(r"^(?P<prefix>.*?src/(?:main|test)/java/)(?P<rest>.+)$")


_TEST_ONLY_IMPORT = re.compile(
    r"^\s*import\s+(?:static\s+)?(?:org\.junit\.|io\.cucumber\.|org\.assertj\.|org\.mockito\.|org\.testcontainers\.|"
    r"org\.springframework\.boot\.test\.|org\.springframework\.boot\.[\w.]*test\.autoconfigure\.|"
    r"org\.springframework\.boot\.resttestclient\.|org\.springframework\.test\.)",
    re.M,
)


def relocate_test_sources(path: str, content: str) -> str:
    """只會出現在測試中的程式碼（JUnit / Cucumber / AssertJ / Mockito / Testcontainers / spring-boot-test）
    若被放到 `src/main/java`，自動改到 `src/test/java`，否則主程式碼編譯時會找不到這些套件。"""
    if "/src/main/java/" in path and path.endswith(".java") and _TEST_ONLY_IMPORT.search(content):
        return path.replace("/src/main/java/", "/src/test/java/", 1)
    return path


def normalize_java_path(path: str, content: str) -> str:
    normalized = relocate_test_sources(path.replace("\\", "/"), content)
    if not normalized.endswith(".java"):
        return normalized
    pkg = _PACKAGE.search(content)
    root = _SRC_ROOT.match(normalized)
    if not pkg or not root:
        return normalized
    file_name = normalized.rsplit("/", 1)[-1]
    expected_rest = pkg.group(1).replace(".", "/") + "/" + file_name
    if root.group("rest") == expected_rest:
        return normalized
    return root.group("prefix") + expected_rest


# 模組目錄 → 允許的套件前綴（相對於基底套件）。bootstrap 另允許根套件（Application 類別）與 bdd。
MODULE_PACKAGES: dict[str, tuple[str, ...]] = {
    "domain": (".domain",),
    "application": (".application",),
    "adapter-web": (".adapter.web",),
    "adapter-persistence": (".adapter.persistence",),
    "bootstrap": (".bootstrap", ".bdd", ""),
}
_MODULE = re.compile(r"^(?P<module>[^/]+)/src/(?:main|test)/java/")


def package_violations(files, base_package: str) -> list[str]:
    """回傳違反「模組目錄對應套件」慣例的說明；空清單代表全部合法。

    例如 adapter-persistence 模組下的類別必須宣告 `package <base>.adapter.persistence[...]`。
    """
    problems: list[str] = []
    for f in files:
        m = _MODULE.match(f.path)
        if not m or not f.path.endswith(".java"):
            continue
        allowed = MODULE_PACKAGES.get(m.group("module"))
        if allowed is None:
            problems.append(f"{f.path}：未知的模組目錄 `{m.group('module')}`，只能是 {', '.join(MODULE_PACKAGES)}")
            continue
        pkg_match = _PACKAGE.search(f.content)
        if not pkg_match:
            problems.append(f"{f.path}：缺少 package 宣告")
            continue
        pkg = pkg_match.group(1)
        expected = [base_package + suffix for suffix in allowed]
        if not any(
            pkg == e or (e and pkg.startswith(e + ".")) or (suffix == "" and pkg == base_package)
            for e, suffix in zip(expected, allowed, strict=True)
        ):
            problems.append(
                f"{f.path}：package `{pkg}` 不屬於模組 `{m.group('module')}` 允許的套件 "
                f"{', '.join(e for e in expected if e)}"
            )
            continue
        # domain 與 adapter 模組不允許子套件（model/、valueobject/、entity/、port/out/ 等會造成 import 混亂）
        if m.group("module") in ("domain", "adapter-web", "adapter-persistence") and pkg not in expected:
            problems.append(
                f"{f.path}：`{m.group('module')}` 模組不得使用子套件，請把類別直接放在 `{expected[0]}`"
                f"（package `{pkg}` 不允許）"
            )
        if m.group("module") == "application" and pkg not in (
            expected[0],
            expected[0] + ".port.in",
            expected[0] + ".port.out",
            expected[0] + ".usecase",
        ):
            problems.append(
                f"{f.path}：application 只允許 `{expected[0]}.port.in` / `.port.out` / `.usecase` 三個子套件"
                f"（package `{pkg}` 不允許）"
            )
    return problems


def duplicate_simple_names(paths) -> list[str]:
    """同一模組內若有兩個不同套件的同名 Java 型別（例如 port.in.DeleteProfilePort 與 port.out.DeleteProfilePort），
    會造成 `reference to X is ambiguous`。回傳說明清單。"""
    by_key: dict[tuple[str, str], list[str]] = {}
    for path in paths:
        m = _MODULE.match(path)
        if not m or not path.endswith(".java") or "/src/test/" in path:
            continue
        simple = path.rsplit("/", 1)[-1][: -len(".java")]
        by_key.setdefault((m.group("module"), simple), []).append(path)
    problems = []
    for (module, simple), found in sorted(by_key.items()):
        if len(found) > 1:
            problems.append(f"模組 `{module}` 內有多個同名型別 `{simple}`：" + "、".join(found))
    return problems


_FRAMEWORK_IMPORT = re.compile(
    r"^\s*import\s+(?:static\s+)?"
    r"((?:org\.springframework|jakarta\.persistence|com\.fasterxml|tools\.jackson|lombok|org\.slf4j|"
    r"org\.apache\.logging|ch\.qos\.logback|java\.util\.logging)\.[\w.*]+)",
    re.M,
)


def framework_violations(files) -> list[str]:
    """domain 與 application 模組的程式碼不得依賴 Spring / JPA / Jackson / Lombok（ArchUnit 也會檢查）。
    在寫入前就拒絕，避免 `@Service` 造成的重複 bean 等間接錯誤。"""
    problems: list[str] = []
    for f in files:
        m = _MODULE.match(f.path)
        if not m or not f.path.endswith(".java") or "/src/test/" in f.path:
            continue
        unavailable = re.search(
            r"^\s*import\s+(?:static\s+)?(org\.apache\.commons|com\.google\.common|lombok|io\.restassured|"
            r"org\.springframework\.hateoas|org\.modelmapper|org\.mapstruct|javax\.)",
            f.content,
            re.M,
        )
        if unavailable:
            problems.append(
                f"{f.path}：classpath 沒有 `{unavailable.group(1)}`"
                "（只能用 JDK、Spring Boot 4 starter、Jakarta、JUnit 6、AssertJ、Mockito、"
                "Cucumber、Testcontainers、ArchUnit）；"
                "請改用 JDK 內建寫法（如 `Objects.requireNonNull`、`String.isBlank()`）"
            )
            continue
        if "org.testcontainers" in f.content or "org.springframework.boot.test" in f.content:
            problems.append(
                f"{f.path}：src/main 不得 import 測試用套件（Testcontainers / spring-boot-test）；"
                "測試設定請放在 src/test，且骨架已提供 TestcontainersConfiguration"
            )
            continue
        if m.group("module") not in ("domain", "application"):
            continue
        hit = _FRAMEWORK_IMPORT.search(f.content)
        if hit:
            problems.append(
                f"{f.path}：{m.group('module')} 模組不得 import `{hit.group(1)}`"
                "（移除 @Service/@Component/@Repository 等註解與 Logger，純 Java 即可，組裝交給 bootstrap）"
            )
    return problems


_MAPPING = re.compile(
    r"@(?:Get|Post|Put|Delete|Patch|Request)Mapping\s*\(\s*(?:value\s*=\s*|path\s*=\s*)?\"(?P<path>[^\"]*)\"[^)]*\)\s*"
    r"(?:@\w+(?:\([^)]*\))?\s*)*"
    r"[\w<>\[\], ?]+\s+\w+\s*\((?P<params>[^)]*)\)",
    re.S,
)
_PATH_VAR = re.compile(
    r"@PathVariable(?:\s*\(\s*(?:(?:value|name)\s*=\s*)?\"(?P<explicit>\w+)\"\s*\))?\s+[\w<>]+\s+(?P<param>\w+)"
)


def path_variable_violations(files) -> list[str]:
    """`@GetMapping(\"/{id}\")` 搭配 `@PathVariable String profileId` 會在執行期以 500 失敗（名稱不一致）。"""
    problems: list[str] = []
    for f in files:
        if not f.path.endswith(".java") or "/src/test/" in f.path:
            continue
        for m in _MAPPING.finditer(f.content):
            template_vars = set(re.findall(r"\{(\w+)(?::[^}]*)?\}", m.group("path")))
            for pv in _PATH_VAR.finditer(m.group("params")):
                name = pv.group("explicit") or pv.group("param")
                if template_vars and name not in template_vars:
                    first = sorted(template_vars)[0]
                    problems.append(
                        f"{f.path}：路徑 `{m.group('path')}` 的變數為 {sorted(template_vars)}，"
                        f"但 `@PathVariable` 綁定的名稱是 `{name}`；"
                        f'請改為 `@PathVariable("{first}")` 或改參數名'
                    )
    return problems


_STRINGS_AND_COMMENTS = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/', re.S)


def truncated_java_files(files) -> list[str]:
    """大括號不平衡的 Java 檔幾乎一定是輸出被截斷（`reached end of file while parsing`）。"""
    problems: list[str] = []
    for f in files:
        if not f.path.endswith(".java"):
            continue
        code = _STRINGS_AND_COMMENTS.sub("", f.content)
        opened, closed = code.count("{"), code.count("}")
        if opened != closed:
            problems.append(f"{f.path}：大括號不平衡（{{ x{opened}，}} x{closed}），檔案疑似被截斷，請完整重新輸出")
    return problems


_STEP_ANNOTATION = re.compile(
    r'(@(?:假如|假設|假定|當|那麼|並且|而且|同時|但是|Given|When|Then|And|But)\(\s*")(?P<pattern>(?:\\.|[^"\\])*)(")'
)
# cucumber-java 的 zh_tw 套件只有：假如 假設 假定 當 那麼 而且 並且 同時 但是；模型常寫出 然後/給定 或簡體寫法
_ZH_TW_STEP_ALIASES = {
    "然後": "那麼",
    "然后": "那麼",
    "那么": "那麼",
    "給定": "假設",
    "给定": "假設",
    "假设": "假設",
    "当": "當",
    "并且": "並且",
    "但": "但是",
}
_ZH_TW_ALIAS_USE = re.compile(
    r"(?P<prefix>io\.cucumber\.java\.zh_tw\.|@)(?P<kw>"
    + "|".join(map(re.escape, _ZH_TW_STEP_ALIASES))
    + r")(?=\b|\(|;|\s)"
)
_BROKEN_STEP_CLOSE = re.compile(
    r'(@(?:假如|假設|假定|當|那麼|並且|而且|同時|但是|Given|When|Then|And|But)\("(?:\\.|[^"\\\n])*?)\\"\)'
)
_UNESCAPED_PAREN = re.compile(r"(?<!\\\\)[()]")
_UNESCAPED_SLASH = re.compile(r"(?<!\\\\)/")
_QUOTED_STRING_PARAM = re.compile(r"""\\"\{string\}\\"|'\{string\}'|「\{string\}」|“\{string\}”""")
_QUOTED_CAPTURE = re.compile(r'\\"\(\[\^\\"\]\*\)\\"|\\"\(\.\*\?\)\\"|\\"\(\.\+\?\)\\"|\\"\(\[\^\\"\]\+\)\\"')


_STEP_ANNOTATION_USE = re.compile(r"@(?:假如|假設|假定|當|那麼|並且|而且|同時|但是|Given|When|Then|And|But)\s*\(")


def is_cucumber_glue(content: str) -> bool:
    """步驟類別的判定：有 io.cucumber import，或直接用了步驟註解（模型常整組 import 都忘了寫）。"""
    return "io.cucumber" in content or bool(_STEP_ANNOTATION_USE.search(content))


def normalize_cucumber_steps(content: str) -> str:
    """把步驟註解中 regex 風格的引號擷取群組（`\"([^\"]*)\"`）改成 Cucumber Expressions 的 `{string}`，
    並移除 `^`/`$` 錨點；否則 Cucumber 會把步驟判定為 undefined。只處理步驤註解字串本身。"""

    def fix(m: re.Match) -> str:
        pattern = m.group("pattern")
        fixed = _QUOTED_CAPTURE.sub("{string}", pattern)
        if fixed.startswith("^"):
            fixed = fixed[1:]
        if fixed.endswith("$") and not fixed.endswith("\\$"):
            fixed = fixed[:-1]
        # 與 .feature 同步：「且其電子郵件為 …」在 feature 端會變成 `並且其電子郵件為 …`，步驟文字不含「且」
        if fixed.startswith("且"):
            fixed = fixed[1:]
        # Cucumber Expressions 把 `/` 當成「替代選項」分隔符（`a/b`），路徑中的斜線必須寫成 `\/`
        # （Java 字串常值裡是 `\\/`）。只處理尚未跳脫的斜線。
        fixed = _UNESCAPED_SLASH.sub(r"\\\\/", fixed)
        # `{string}` 已包含引號；再用 \"{string}\" 或 '{string}' 包起來就永遠比對不到
        fixed = _QUOTED_STRING_PARAM.sub("{string}", fixed)
        # 與 .feature 同步：狀態碼後面的括號說明一律移除（見 _STATUS_LABEL_IN_FEATURE）
        fixed = _STATUS_LABEL_IN_STEP.sub(r"\1", fixed)
        # Cucumber Expressions 的 `( )` 是「可省略文字」語法且不可含參數；模型寫的括號幾乎都是字面意義 → 跳脫
        fixed = _UNESCAPED_PAREN.sub(lambda pm: "\\\\" + pm.group(0), fixed)
        return f"{m.group(1)}{fixed}{m.group(3)}"

    content = _ZH_TW_ALIAS_USE.sub(lambda m: m.group("prefix") + _ZH_TW_STEP_ALIASES[m.group("kw")], content)
    # 模型偶爾把結尾引號寫成 `\")`（unclosed string literal）；步驟註解以 `\")` 收尾只可能是這種筆誤
    content = _BROKEN_STEP_CLOSE.sub(lambda m: m.group(1) + '")', content)
    # 別名替換後可能出現重複 import（例如原本同時 import 然後 與 那麼），去重
    seen: set[str] = set()
    lines: list[str] = []
    for line in content.splitlines(keepends=True):
        if line.startswith("import io.cucumber.java.zh_tw."):
            if line in seen:
                continue
            seen.add(line)
        lines.append(line)
    content = "".join(lines)
    content = _STEP_ANNOTATION.sub(fix, content)
    return _ensure_zh_tw_step_imports(content)


_ZH_TW_STEP_USE = re.compile(r"@(假如|假設|假定|當|那麼|並且|而且|同時|但是)\s*\(")


def _ensure_zh_tw_step_imports(content: str) -> str:
    """模型常用了 `@並且` 卻只 import 假設/當/那麼 → `cannot find symbol: class 並且`，接著連續多輪修不好。
    對檔案裡實際用到的每個 zh-TW 步驟註解補齊 `import io.cucumber.java.zh_tw.X;`。"""
    pkg = _PACKAGE_LINE.search(content)
    if not pkg or not is_cucumber_glue(content):
        return content
    needed = ""
    for kw in sorted(set(_ZH_TW_STEP_USE.findall(content))):
        stmt = f"import io.cucumber.java.zh_tw.{kw};\n"
        if stmt not in content:
            needed += stmt
    if not needed:
        return content
    return content[: pkg.end()] + "\n" + needed + content[pkg.end() :]


_STEREOTYPE_IMPORT = re.compile(
    r"^\s*import\s+org\.springframework\.(?:stereotype\.(?:Service|Component|Repository)|"
    r"transaction\.annotation\.(?:Transactional|Propagation)|beans\.factory\.annotation\.(?:Autowired|Qualifier)|"
    r"lang\.(?:NonNull|Nullable|NonNullApi)|context\.annotation\.(?:Primary|Lazy)|validation\.annotation\.Validated)"
    r"\s*;\s*\n",
    re.M,
)
_STEREOTYPE_ANNOTATION = re.compile(
    r"^\s*@(?:Service|Component|Repository|Transactional|Autowired|Qualifier|Primary|Lazy|Validated)(?:\([^)]*\))?\s*\n",
    re.M,
)
_INLINE_ANNOTATION = re.compile(r"@(?:NonNull|Nullable)\s+")


def strip_spring_stereotypes(path: str, content: str) -> str:
    """domain / application 的主程式碼中，`@Service`/`@Component`/`@Transactional`/`@Autowired` 可安全移除
    （組裝由 bootstrap 負責），直接自動修正，避免模型反覆加回去。"""
    m = _MODULE.match(path)
    if (
        not m
        or m.group("module") not in ("domain", "application")
        or "/src/test/" in path
        or not path.endswith(".java")
    ):
        return content
    content = _STEREOTYPE_IMPORT.sub("", content)
    content = _STEREOTYPE_ANNOTATION.sub("", content)
    return _INLINE_ANNOTATION.sub("", content)


_BUILTIN_PARAM_TYPES = {
    "int",
    "float",
    "word",
    "string",
    "bigdecimal",
    "biginteger",
    "byte",
    "short",
    "long",
    "double",
    "",
}


_FIXED_ID_PRECONDITION = re.compile(
    r'@(?:假設|假如|假定|Given)\(\s*"[^"]*ID\s*為\s*\{string\}[^"]*"\s*\)\s*\n(?:\s*@\w+(?:\([^)]*\))?\s*\n)*'
    r"\s*(?:public\s+)?[\w<>\[\]]+\s+(?P<name>\w+)\s*\("
)
_HTTP_CREATE_CALL = re.compile(r"\b(?:postForEntity|postForObject|postForLocation)\s*\(|HttpMethod\.POST\b")
_DIRECT_PERSIST = re.compile(r"\b\w*(?:Port|Repository|UseCase|EntityManager)\b")


def cucumber_precondition_violations(files) -> list[str]:
    """「假設 已存在 ID 為 {string} 的個人資料」若用 POST 建資料，伺服器會自己產生 id，固定 UUID 永遠不存在，
    之後的 GET/PUT/DELETE 全部 404、重複 email 也回 201；本機模型連續多個 run 都這樣寫並且修不好。
    要求前置步驟直接透過 Port / repository 以指定 id 儲存。"""
    out: list[str] = []
    for f in files:
        if "/src/test/" not in f.path or not f.path.endswith(".java") or "io.cucumber.java" not in f.content:
            continue
        bodies = _method_bodies(f.content)
        for m in _FIXED_ID_PRECONDITION.finditer(f.content):
            name = m.group("name")
            body = bodies.get(name, "")
            reach = body
            for called in set(_CALL_NAME.findall(body)):
                if called != name and called in bodies:
                    reach += bodies[called]
            if _HTTP_CREATE_CALL.search(reach) and not _DIRECT_PERSIST.search(reach):
                out.append(
                    f"{f.path}#{name}()：前置步驟用 POST 建資料無法指定 id（伺服器自行產生），"
                    "固定 UUID 之後查詢會 404。"
                    "請 @Autowired 應用層的 SaveProfilePort（或 JPA repository）直接以該 id 建立並儲存"
                )
    return out


def cucumber_step_violations(files) -> list[str]:
    """Cucumber Expressions 只接受內建參數型別；自創 `{uuid}`/`{email}` 會讓整個 glue 類別無法註冊，
    所有步驟都變成 undefined。"""
    problems: list[str] = []
    for f in files:
        if not f.path.endswith(".java") or "/src/test/" not in f.path or "io.cucumber.java" not in f.content:
            continue
        for m in _STEP_ANNOTATION.finditer(f.content):
            for param in re.findall(r"\{(\w*)\}", m.group("pattern")):
                if param not in _BUILTIN_PARAM_TYPES:
                    problems.append(
                        f"{f.path}：步驤樣板 `{m.group('pattern')}` 使用了未註冊的參數型別 `{{{param}}}`；"
                        "請改用 `{string}` 並在方法內以 `UUID.fromString(...)` 等方式轉換"
                    )
    return problems


_CLASS_DECL = re.compile(r"^(?P<indent>[ \t]*)(?:public\s+)?(?:final\s+)?class\s+\w+", re.M)
_PACKAGE_LINE = re.compile(r"^package\s+[\w.]+\s*;\s*\n", re.M)
_JPA_TEST_DIRTIES_IMPORT = "import org.springframework.test.annotation.DirtiesContext;\n"
_JPA_TEST_TX_IMPORTS = (
    "import org.springframework.transaction.annotation.Propagation;\n"
    "import org.springframework.transaction.annotation.Transactional;\n"
)
_JPA_TEST_DIRTIES_ANNOTATION = "@DirtiesContext(classMode = DirtiesContext.ClassMode.AFTER_EACH_TEST_METHOD)\n"
_JPA_TEST_NON_TX_ANNOTATIONS = (
    "// 每次 repository 呼叫各自交易：避免一級快取與交易中止問題；每個測試後重建 context 與 schema\n"
    "@Transactional(propagation = Propagation.NOT_SUPPORTED)\n"
)
_NOT_SUPPORTED_ANNOTATION = re.compile(
    r"[ \t]*@(?:org\.springframework\.transaction\.annotation\.)?Transactional\(\s*propagation\s*=\s*"
    r"(?:org\.springframework\.transaction\.annotation\.)?(?:Propagation\.)?NOT_SUPPORTED\s*\)[ \t]*\n?"
)
_JPA_TEST_TX_COMMENT = (
    "// 測試使用 TestEntityManager（需要交易）：保留測試交易，以 flush()/clear() 控制一級快取；每個測試後重建 context\n"
)


def normalize_data_jpa_tests(path: str, content: str) -> str:
    """adapter-persistence 的 `@DataJpaTest` 測試在每個測試後重建 context；
    若測試未使用 `TestEntityManager`，另改為非交易式執行，讓「刪除後查詢」「唯一約束違反」等情境不受單一測試交易影響。
    使用 `TestEntityManager` 的測試必須保留交易（否則 `No transactional EntityManager found`），
    由測試自行 flush()/clear()。"""
    if not path.startswith("adapter-persistence/src/test/") or "@DataJpaTest" not in content:
        return content
    content = _strip_repository_imports(content)
    # TestEntityManager 與直接注入的 jakarta.persistence.EntityManager 都需要交易
    # （persist/flush 會丟 TransactionRequiredException）
    uses_test_entity_manager = "EntityManager" in content
    if uses_test_entity_manager and _NOT_SUPPORTED_ANNOTATION.search(content):
        # 類別或方法層級的非交易設定（含完整套件名寫法）會讓 TestEntityManager 失效，移除之
        content = _NOT_SUPPORTED_ANNOTATION.sub("", content)
        content = content.replace(_JPA_TEST_NON_TX_ANNOTATIONS.splitlines(keepends=True)[0], "")
    already_dirties = "@DirtiesContext" in content
    if already_dirties and (uses_test_entity_manager or "NOT_SUPPORTED" in content):
        return _ensure_jpa_test_imports(content)
    decl = _CLASS_DECL.search(content)
    pkg = _PACKAGE_LINE.search(content)
    if not decl or not pkg:
        return content
    # 找到 class 宣告之前的註解區塊起點（第一個 @ 開頭的行）
    head = content[: decl.start()]
    lines = head.splitlines(keepends=True)
    insert_at = len(head)
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].lstrip().startswith("@"):
            insert_at = sum(len(x) for x in lines[:i])
        elif lines[i].strip():
            break
        elif insert_at != len(head):
            break  # 註解區塊之前的空白行：不再往前移
    extra = ""
    imports = ""
    if uses_test_entity_manager:
        extra += _JPA_TEST_TX_COMMENT
    elif "NOT_SUPPORTED" not in content:
        extra += _JPA_TEST_NON_TX_ANNOTATIONS
        imports += _JPA_TEST_TX_IMPORTS
    if not already_dirties:
        extra += _JPA_TEST_DIRTIES_ANNOTATION
        imports += _JPA_TEST_DIRTIES_IMPORT
    content = content[:insert_at] + extra + content[insert_at:]
    missing = "".join(line for line in imports.splitlines(keepends=True) if line not in content)
    content = content[: pkg.end()] + "\n" + missing + content[pkg.end() :]
    return _ensure_jpa_test_imports(content)


def _ensure_jpa_test_imports(content: str) -> str:
    """不論註解是誰加的（normalizer 或模型照抄），都補齊對應 import（模型常只抄註解不抄 import）。"""
    pkg = _PACKAGE_LINE.search(content)
    if not pkg:
        return content
    needed = ""
    if "@DirtiesContext" in content and _JPA_TEST_DIRTIES_IMPORT not in content:
        needed += _JPA_TEST_DIRTIES_IMPORT
    if "Propagation.NOT_SUPPORTED" in content:
        for line in _JPA_TEST_TX_IMPORTS.splitlines(keepends=True):
            if line not in content:
                needed += line
    if not needed:
        return content
    return content[: pkg.end()] + "\n" + needed + content[pkg.end() :]


def normalize_boot_tests(path: str, content: str) -> str:
    """bootstrap 的 `@SpringBootTest` 測試若沒掛 TestcontainersConfiguration 就沒有資料庫
    （`Unable to determine Dialect`），自動補上 `@Import(<base>.bootstrap.TestcontainersConfiguration.class)`。"""
    if not path.startswith("bootstrap/src/test/") or "@SpringBootTest" not in content:
        return content
    if "TestcontainersConfiguration" in content:
        return content
    pkg = _PACKAGE.search(content)
    decl = _CLASS_DECL.search(content)
    if not pkg or not decl:
        return content
    base = pkg.group(1).rsplit(".", 1)[0]  # <base>.bootstrap / <base>.bdd → <base>
    annotation = f"@Import({base}.bootstrap.TestcontainersConfiguration.class)  // 自動補上：測試需要 PostgreSQL 容器\n"
    head = content[: decl.start()]
    lines = head.splitlines(keepends=True)
    insert_at = len(head)
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].lstrip().startswith("@"):
            insert_at = sum(len(x) for x in lines[:i])
        elif lines[i].strip():
            break
        elif insert_at != len(head):
            break  # 註解區塊之前的空白行：不再往前移
    content = content[:insert_at] + annotation + content[insert_at:]
    if "import org.springframework.context.annotation.Import;" not in content:
        pkg_line = _PACKAGE_LINE.search(content)
        if pkg_line:
            content = (
                content[: pkg_line.end()]
                + "\nimport org.springframework.context.annotation.Import;\n"
                + content[pkg_line.end() :]
            )
    return content


_STEP_STEREOTYPE_IMPORT = re.compile(
    r"^\s*import\s+org\.springframework\.stereotype\.(?:Component|Service|Repository)\s*;\s*\n", re.M
)
_STEP_STEREOTYPE_ANNOTATION = re.compile(r"^\s*@(?:Component|Service|Repository)(?:\([^)]*\))?\s*\n", re.M)


_STEP_METHOD_DECL = re.compile(
    r"^(?P<indent>[ \t]*)(?=\S)(?!public\b|private\b|protected\b|@)(?P<decl>(?:static\s+|final\s+|synchronized\s+)*"
    r"[\w<>\[\], ?]+\s+\w+\s*\()",
    re.M,
)


def publicize_cucumber_steps(path: str, content: str) -> str:
    """Cucumber 的 classpath 掃描只註冊 **public** 的 glue 類別，而 `Class.getMethods()` 也只回傳 **public** 方法；
    package-private 的步驟類別或步驟方法都會被靜默忽略，結果是每一個步驟都 `undefined`（骨架的 SmokeSteps 卻正常）。
    把頂層步驟類別與每個步驟註解後面的方法宣告都改成 public。"""
    if not (path.endswith(".java") and "/src/test/" in path and is_cucumber_glue(content)):
        return content
    content = _publicize_missing(content)
    lines = content.split("\n")
    pending = False
    for i, line in enumerate(lines):
        if _STEP_ANNOTATION_LINE.match(line + "\n"):
            pending = True
            continue
        if not pending or not line.strip():
            continue
        if line.lstrip().startswith("@"):
            continue  # 其他註解（@Transactional 等）夾在中間
        m = _STEP_METHOD_DECL.match(line)
        if m:
            lines[i] = f"{m.group('indent')}public {line[len(m.group('indent')) :]}"
        pending = False
    return "\n".join(lines)


def strip_cucumber_step_stereotypes(path: str, content: str) -> str:
    """Cucumber 步驟類別由 cucumber-spring 註冊，不可再標 `@Component`：
    否則 `@SpringBootTest` 的 component scan 會把它當一般 bean 建立，而 `TestRestTemplate` 在該 context 中不存在，
    導致骨架的 ApplicationContextTest 啟動失敗。"""
    if not (path.endswith(".java") and "/src/test/" in path and is_cucumber_glue(content)):
        return content
    content = _STEP_STEREOTYPE_ANNOTATION.sub("", content)
    return _STEP_STEREOTYPE_IMPORT.sub("", content)


# zh-TW Gherkin 方言只接受以下關鍵字；模型常混用 zh-CN 或自創關鍵字（如「範例」「情境」）
_GHERKIN_ZH_TW_ALIASES = {
    "範例": "例子",
    "示例": "例子",
    "例子表": "例子",
    "情境大綱": "場景大綱",
    "情境": "場景",
    "剧本": "場景",
    "场景大纲": "場景大綱",
    "场景": "場景",
    "功能說明": "功能",
    "特性": "功能",
    "規則": "Rule",
    "规则": "Rule",
}
_GHERKIN_KEYWORD_LINE = re.compile(r"^(?P<indent>\s*)(?P<kw>[^\s:：#|@\"]+?)(?P<colon>[:：])(?P<rest>.*)$", re.M)
_GHERKIN_STEP_ALIASES = {
    "給定": "假設",
    "假如": "假如",
    "给定": "假設",
    "当": "當",
    "那么": "那麼",
    "然後": "那麼",
    "然后": "那麼",
    "而且": "而且",
    "并且": "並且",
    "但": "但是",
    # 模型常把接續條件寫成「且其電子郵件為 …」（沒有關鍵字的新行）；視為 並且 步驟，步驟定義端同步去掉開頭的「且」
    "且": "並且",
}
_GHERKIN_STEP_LINE = re.compile(
    r"^(?P<indent>\s*)(?P<kw>給定|给定|当|那么|然後|然后|并且|但(?!是)|且)(?P<rest>\s.*|[^\s].*)$", re.M
)


def normalize_gherkin_zh_tw(path: str, content: str) -> str:
    """把 `.feature` 檔中的非 zh-TW 方言關鍵字改成 zh-TW 寫法（例如 `範例:` → `例子:`），避免 Gherkin 解析失敗。
    只處理 `# language: zh-TW` 的檔案（或未宣告語言但含中文關鍵字者，並補上宣告）。"""
    if not path.endswith(".feature"):
        return content
    if "# language:" in content and "zh-TW" not in content.split("\n", 3)[0:3].__str__():
        return content

    def fix_kw(m: re.Match) -> str:
        kw = m.group("kw")
        return f"{m.group('indent')}{_GHERKIN_ZH_TW_ALIASES.get(kw, kw)}:{m.group('rest')}"

    content = _GHERKIN_KEYWORD_LINE.sub(fix_kw, content)
    content = _GHERKIN_STEP_LINE.sub(
        lambda m: f"{m.group('indent')}{_GHERKIN_STEP_ALIASES[m.group('kw')]}{m.group('rest')}", content
    )
    content = _fold_gherkin_continuations(content)
    content = _STATUS_LABEL_IN_FEATURE.sub(r"\1", content)
    content = _strip_trailing_step_notes(content)
    content = _promote_scenarios_with_examples(content)
    content = _substitute_vars_in_plain_scenarios(content)
    content = _replace_symbolic_ids(content)
    content = _dedupe_emails_across_scenarios(content)
    if "# language:" not in content and re.search(r"^\s*功能:", content, re.M):
        content = "# language: zh-TW\n" + content
    return content


# `回應的狀態碼應該是 404 (Not Found)`：括號內的狀態文字讓步驟定義幾乎不可能逐字對應（`{word}` 吃不下兩個字）→ 一律移除
_STATUS_LABEL_IN_FEATURE = re.compile(r"(\b[1-5]\d\d)\s*[（(][A-Za-z][A-Za-z ]*[)）]")
_STATUS_LABEL_IN_STEP = re.compile(r"(\{int\})\s*(?:\\\\)?[（(][^)）]*(?:\\\\)?[)）]")
_CJK_EDGE = re.compile(r"^[\u3400-\u9fff\uf900-\ufaff]")
_GHERKIN_STEP_START = re.compile(r"^\s*(?:假如|假設|假定|當|那麼|而且|並且|同時|但是|\*)")
_GHERKIN_BLOCK_START = re.compile(r"^\s*(?:功能|背景|場景大綱|劇本大綱|場景|劇本|例子|Rule)\s*[:：]")


def _fold_gherkin_continuations(content: str) -> str:
    """Gherkin 每個步驧必須寫在同一行；模型常把長步驟折成兩行（第二行沒有關鍵字），
    解析會得到 `expected: #StepLine ... got '...'`。把這種接續行併回前一個步驟行。"""
    out: list[str] = []
    in_docstring = False
    prev_is_step = False
    seen_scenario = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith(('"""', "```")):
            in_docstring = not in_docstring
            out.append(line)
            prev_is_step = False
            continue
        if in_docstring or not stripped:
            out.append(line)
            if not in_docstring:
                prev_is_step = False
            continue
        if _GHERKIN_BLOCK_START.match(line):
            seen_scenario = seen_scenario or not stripped.startswith("功能")
            out.append(line)
            prev_is_step = False
            continue
        if stripped.startswith(("|", "#", "@")):
            out.append(line)
            prev_is_step = False
            continue
        if _GHERKIN_STEP_START.match(line):
            out.append(line)
            prev_is_step = True
            continue
        if seen_scenario and prev_is_step and out:
            prev = out[-1].rstrip()
            # 中文句子之間模型在步驟定義裡幾乎都寫全形逗號；兩側皆為 CJK 字元時用「，」連接，否則用空白
            joiner = (
                "，" if _CJK_EDGE.match(stripped) and _CJK_EDGE.match(prev[-1:]) and not prev.endswith("，") else " "
            )
            out[-1] = prev + joiner + stripped
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if content.endswith("\n") else "")


_INTERFACE_DECL = re.compile(r"\binterface\s+(?P<name>\w+)")
_IMPLEMENTS_CLAUSE = re.compile(r"\bimplements\s+(?P<list>[^{]+)\{")


def unimplemented_use_cases(main_sources: Mapping[str, str]) -> list[str]:
    """回傳 `application/port/in` 中尚無任何實作類別的 inbound port 介面名稱。
    模型常在「Application Ports & Use Cases」任務只實作其中一個 use case 就讓測試通過，
    之後 bootstrap 接線時才發現缺類別；在輸出被接受前就擋下來。"""
    interfaces: set[str] = set()
    for path, content in main_sources.items():
        if "/src/main/" in path and "/application/port/in/" in path and path.endswith(".java"):
            m = _INTERFACE_DECL.search(content)
            if m:
                interfaces.add(m.group("name"))
    if not interfaces:
        return []
    implemented: set[str] = set()
    for path, content in main_sources.items():
        if "/src/main/" not in path or not path.endswith(".java"):
            continue
        for m in _IMPLEMENTS_CLAUSE.finditer(content):
            for raw in m.group("list").split(","):
                implemented.add(raw.strip().split("<")[0].split(".")[-1])
    return sorted(interfaces - implemented)


# Spring Boot 3 / Testcontainers 1 / Jackson 2 的 import 路徑 → Spring Boot 4 / Testcontainers 2 / Jackson 3
_LEGACY_IMPORTS: dict[str, str] = {
    "org.springframework.boot.test.web.client.TestRestTemplate": (
        "org.springframework.boot.resttestclient.TestRestTemplate"
    ),
    "org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest": (
        "org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest"
    ),
    "org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc": (
        "org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc"
    ),
    "org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest": (
        "org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest"
    ),
    "org.springframework.boot.test.autoconfigure.orm.jpa.TestEntityManager": (
        "org.springframework.boot.jpa.test.autoconfigure.TestEntityManager"
    ),
    "org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase": (
        "org.springframework.boot.jdbc.test.autoconfigure.AutoConfigureTestDatabase"
    ),
    "org.springframework.boot.autoconfigure.domain.EntityScan": (
        "org.springframework.boot.persistence.autoconfigure.EntityScan"
    ),
    "org.testcontainers.containers.PostgreSQLContainer": "org.testcontainers.postgresql.PostgreSQLContainer",
}
_LEGACY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("com.fasterxml.jackson.databind.", "tools.jackson.databind."),
    ("com.fasterxml.jackson.core.", "tools.jackson.core."),
)
_IMPORT_LINE = re.compile(r"^(?P<head>\s*import\s+(?:static\s+)?)(?P<fqcn>[\w.]+)(?P<tail>\s*;)", re.M)


def rewrite_legacy_imports(path: str, content: str) -> str:
    """把模型從 Spring Boot 3 / Testcontainers 1 / Jackson 2 記憶中帶出來的 import 改成目前 classpath 的套件路徑。"""
    if not path.endswith(".java"):
        return content

    def fix(m: re.Match) -> str:
        fqcn = m.group("fqcn")
        target = _LEGACY_IMPORTS.get(fqcn)
        if target is None:
            for old, new in _LEGACY_PREFIXES:
                if fqcn.startswith(old):
                    target = new + fqcn[len(old) :]
                    break
        if target is None:
            return m.group(0)
        return f"{m.group('head')}{target}{m.group('tail')}"

    content = _IMPORT_LINE.sub(fix, content)
    # 註解中常見的完整套件名寫法（例如 @org.springframework.boot.test.web.client.TestRestTemplate）
    for old, new in _LEGACY_IMPORTS.items():
        content = content.replace(old, new)
    for old, new in _LEGACY_PREFIXES:
        content = content.replace(old, new)
    return content


_NULL_HTTP_ENTITY = re.compile(
    r"new\s+(?:org\.springframework\.http\.)?HttpEntity<(?P<type>[\w.<>?, ]+)>\(\s*null\s*\)"
)
_NULL_HTTP_ENTITY_DIAMOND = re.compile(r"new\s+(?:org\.springframework\.http\.)?HttpEntity<>\(\s*null\s*\)")


def fix_null_http_entity(path: str, content: str) -> str:
    """`new HttpEntity<Void>(null)` 會因 `HttpEntity(HttpHeaders)` 與 `HttpEntity(MultiValueMap)` 皆匹配而編譯失敗
    （`reference to HttpEntity is ambiguous`）；本機模型反覆修不好。改成明確轉型的 body 參數。"""
    if not path.endswith(".java"):
        return content
    content = _NULL_HTTP_ENTITY.sub(lambda m: f"new HttpEntity<{m.group('type')}>(({m.group('type')}) null)", content)
    return _NULL_HTTP_ENTITY_DIAMOND.sub("new HttpEntity<Void>((Void) null)", content)


_STUB_MARKER = re.compile(
    r"UnsupportedOperationException\s*\(\s*\"[^\"]*"
    r"(?:[Nn]ot (?:yet )?implemented|TODO|TDD|snippet|placeholder|stub|尚未實作|未實作)"
    r"|//\s*TODO[:：]?\s*(?:implement|實作)"
    r"|throw new (?:RuntimeException|IllegalStateException)\s*\(\s*\"(?:TODO|Not implemented)",
    re.I,
)


def stub_violations(files) -> list[str]:
    """GREEN / 修正階段的主程式碼不得留下「尚未實作」佔位
    （模型常為了讓測試編譯而寫 `throw new UnsupportedOperationException(...)`，之後再也不回頭補實作）。
    回傳有問題的檔案路徑。"""
    return sorted(
        f.path for f in files if "/src/main/" in f.path and f.path.endswith(".java") and _STUB_MARKER.search(f.content)
    )


_EMAIL_LITERAL = re.compile(r'"([\w.+-]+@[\w-]+(?:\.[\w-]+)+)"')
_METHOD_DECL = re.compile(
    r"^[ \t]+(?:(?:public|private|protected|static|final)\s+)*[\w<>\[\], ?]+\s+(?P<name>\w+)\s*\([^)]*\)"
    r"\s*(?:throws\s+[\w., ]+)?\s*\{",
    re.M,
)
_DUPLICATE_EXCEPTION = re.compile(r"\bDuplicate\w*Exception\b")
_CALL_NAME = re.compile(r"\b(\w+)\s*\(")
_MOCK_THROW = re.compile(r"\b(?:thenThrow|doThrow|willThrow|given)\s*\(")


def _method_bodies(content: str) -> dict[str, str]:
    bodies: dict[str, str] = {}
    for m in _METHOD_DECL.finditer(content):
        end = _enclosing_block_end(content, m.end())
        if end != -1:
            bodies.setdefault(m.group("name"), content[m.end() : end])
    return bodies


def uniqueness_test_violations(files) -> list[str]:
    """唯一性測試（斷言 `Duplicate*Exception`）裡兩筆資料若用了不同 email，唯一約束永遠不會觸發，
    測試只會得到 `Expecting code to raise a throwable`；本機模型會連續十次改 Port 實作而不檢查測試本身
    （常見寫法：helper 寫死 `test@example.com`，第二筆卻傳 `dup@test.com`）。
    連同該測試方法呼叫到的同檔 helper 一起蒐集 email 字串常值，出現兩個以上即視為錯誤。"""
    out: list[str] = []
    for f in files:
        # 只檢查 adapter-persistence 的測試：唯一約束只有真實資料庫會觸發；
        # application 層的 Mockito 測試是用 thenThrow 模擬 Port 丟例外，兩筆資料 email 不同是正常的
        if not f.path.startswith("adapter-persistence/src/test/") or not f.path.endswith(".java"):
            continue
        bodies = _method_bodies(f.content)
        for name, body in bodies.items():
            if "assertThatThrownBy" not in body or not _DUPLICATE_EXCEPTION.search(body):
                continue
            if _MOCK_THROW.search(body):
                continue
            emails = set(_EMAIL_LITERAL.findall(body))
            for called in set(_CALL_NAME.findall(body)):
                if called != name and called in bodies:
                    emails.update(_EMAIL_LITERAL.findall(bodies[called]))
            if len(emails) >= 2:
                out.append(
                    f"{f.path}#{name}()：唯一性測試中兩筆資料的 email 不同（{', '.join(sorted(emails))}），"
                    "不會觸發唯一約束。請修正**測試**：兩筆資料必須使用同一個 email 字串，只有 id 不同"
                )
    return out


_TOP_LEVEL_TYPE = re.compile(
    r"^(?P<mods>(?:(?:final|abstract|sealed|non-sealed|static)\s+)*)"
    r"(?P<kind>class|interface|record|enum)\s+(?P<name>\w+)",
    re.M,
)
_ADAPTER_MAIN = re.compile(r"^adapter-[\w-]+/src/main/java/")


def publicize_adapter_types(path: str, content: str) -> str:
    """adapter 模組的頂層型別一律 public：bootstrap 的 `UseCaseConfiguration` 常直接引用它們，
    package-private 會造成 `X is not public in ...; cannot be accessed from outside package`，本機模型多次修不好。"""
    if not path.endswith(".java") or not _ADAPTER_MAIN.match(path):
        return content

    def fix(m: re.Match) -> str:
        return f"public {m.group('mods')}{m.group('kind')} {m.group('name')}"

    # 只處理行首（頂層）宣告；巢狀型別前面有縮排，不會被 ^ 匹配
    return (
        _TOP_LEVEL_TYPE.sub(fix, content, count=0)
        if not re.search(r"^public\s", content, re.M)
        else _publicize_missing(content)
    )


def _publicize_missing(content: str) -> str:
    out = []
    for line in content.splitlines(keepends=True):
        m = _TOP_LEVEL_TYPE.match(line)
        if m and not line.startswith(("public", "private", "protected")):
            line = f"public {line}"
        out.append(line)
    return "".join(out)


_ENTITY_MANAGER_USE = re.compile(r"^\s*import\s+jakarta\.persistence\.EntityManager\s*;", re.M)


_SAVE_PORT_IMPL = re.compile(r"\bimplements\s+[\w,\s]*\b\w*Save\w*Port\b")
_REPO_SAVE_CALL = re.compile(r"\.save(?:AndFlush)?\s*\(")
_REPO_LOOKUP_CALL = re.compile(r"\.(?:findById|existsById|getReferenceById|findByIdOrNull)\s*\(")


def persistence_violations(files) -> list[str]:
    """adapter-persistence 的 Port 實作必須透過 Spring Data `JpaRepository`
    （`saveAndFlush` 才會立刻觸發唯一約束並轉成 `DataIntegrityViolationException`）；
    直接注入 `EntityManager.merge/persist` 不會 flush，唯一性測試永遠不會拋例外。
    另外 Save Port 若只是把 domain 物件轉成新 Entity 後 `save`，更新既有 id 時會因 `Persistable.isNew()==true`
    被當成 INSERT 撞主鍵，再被轉成 `DuplicateEmailException`（Cucumber 更新場景 `expected 200 but was 409`）；
    save() 必須先 `findById`（存在則更新受管 Entity 欄位）。"""
    out: list[str] = []
    for f in files:
        if not f.path.startswith("adapter-persistence/src/main/") or not f.path.endswith(".java"):
            continue
        if _ENTITY_MANAGER_USE.search(f.content):
            out.append(
                f"{f.path}：不要直接注入 jakarta.persistence.EntityManager，"
                "改用 Spring Data `JpaRepository` 並以 `saveAndFlush` 儲存"
            )
        elif (
            _SAVE_PORT_IMPL.search(f.content)
            and _REPO_SAVE_CALL.search(f.content)
            and not _REPO_LOOKUP_CALL.search(f.content)
        ):
            out.append(
                f"{f.path}：Save Port 的 save() 直接 `save(toEntity(profile))` "
                "會把既有 id 當成新資料 INSERT（isNew==true）而撞主鍵，"
                "更新會變成 409。請先 `repository.findById(id)`：存在就用 setter 更新該受管 Entity 再 `saveAndFlush`，"
                "不存在才建立新 Entity"
            )
    return sorted(out)


# Spring Framework 7 已移除的 API → 現行寫法（純文字層級替換，不改語意）
_REMOVED_API_REWRITES: tuple[tuple[str, str], ...] = (
    (".getStatusCodeValue()", ".getStatusCode().value()"),
    (".getRawStatusCode()", ".getStatusCode().value()"),
    (".toHttpStatus()", ""),
)
_EM_FIND_BY_ID = re.compile(r"\b(\w*[eE]ntityManager)\.findById\(")
# Spring 7 的 HttpHeaders 不再實作 Map：`headers.containsKey("Location")` → `containsHeader`
_HEADERS_CONTAINS_KEY = re.compile(r"\b(\w*[Hh]eaders(?:\(\))?)\.containsKey\(")
_NULL_ARGUMENTS = re.compile(r"\b(Arguments\.of|arguments)\(\s*null\s*\)")


def rewrite_removed_apis(path: str, content: str) -> str:
    """把模型從舊版 Spring 記憶中帶出來、在 Spring 7 已不存在的方法改寫成現行 API。"""
    if not path.endswith(".java"):
        return content
    for old, new in _REMOVED_API_REWRITES:
        content = content.replace(old, new)
    content = _HEADERS_CONTAINS_KEY.sub(r"\1.containsHeader(", content)
    if "/src/test/" in path:
        # JUnit：`Arguments.of(null)` 會把 null 當成整個 varargs 陣列 → `arguments array must not be null`
        content = _NULL_ARGUMENTS.sub(lambda m: f"{m.group(1)}((Object) null)", content)
        # TestEntityManager / EntityManager 沒有 findById，正確是 find(Class, id)
        content = _EM_FIND_BY_ID.sub(r"\1.find(", content)
    return content


_ADAPTER_PORT_IMPL = re.compile(
    r"^(?P<decl>(?:public\s+)?(?:final\s+)?class\s+\w+(?:<[^>]*>)?(?:\s+extends\s+[\w.<>]+)?\s+implements\s+[^{]*\w*Port\b[^{]*\{)",
    re.M,
)
_HAS_STEREOTYPE = re.compile(
    r"^\s*@(?:Component|Service|Repository|RestController|Controller|Configuration|ControllerAdvice|RestControllerAdvice)\b",
    re.M,
)
_PORT_IMPL_HINT = re.compile(r"implements\s+[^{]*\b\w*Port\b")


def ensure_adapter_components(path: str, content: str) -> str:
    """adapter 模組中實作 `*Port` 介面的類別必須是 Spring bean（`@Component`），
    否則 bootstrap 的 `UseCaseConfiguration` 注入時會 `No qualifying bean of type ...`。模型常漏掉這個註解。"""
    if not path.endswith(".java") or not _ADAPTER_MAIN.match(path):
        return content
    if "/src/test/" in path or _HAS_STEREOTYPE.search(content) or not _PORT_IMPL_HINT.search(content):
        return content
    m = _ADAPTER_PORT_IMPL.search(content)
    if not m:
        return content
    content = content[: m.start()] + "@Component\n" + content[m.start() :]
    if "import org.springframework.stereotype.Component;" not in content:
        pkg = _PACKAGE_LINE.search(content)
        if pkg:
            content = (
                content[: pkg.end()] + "\n\nimport org.springframework.stereotype.Component;" + content[pkg.end() :]
            )
    return content


_MOCKITO_USE = re.compile(
    r"\b(?:doNothing|doThrow|doReturn|doAnswer|when|verify|verifyNoInteractions|verifyNoMoreInteractions|never|times|mock|spy|any|anyString|anyInt|anyLong|eq|argThat)\("
)
_ASSERTJ_USE = re.compile(
    r"\b(?:assertThat|assertThatThrownBy|assertThatCode|assertThatExceptionOfType|assertThatNoException)\("
)
_MOCKMVC_REQUEST_USE = re.compile(r"\bperform\(\s*(?:get|post|put|delete|patch)\(")
_MOCKMVC_RESULT_USE = re.compile(r"\b(?:status|jsonPath|content|header|redirectedUrl)\(\)?")


def ensure_test_static_imports(path: str, content: str) -> str:
    """測試程式碼若使用 Mockito / AssertJ / MockMvc 的靜態方法卻忘了 `import static`，補上 on-demand 匯入
    （`cannot find symbol: method doNothing()` 是本機模型最常見的測試編譯錯誤之一）。"""
    if not path.endswith(".java") or "/src/test/" not in path:
        return content
    pkg = _PACKAGE_LINE.search(content)
    if not pkg:
        return content
    body = content[pkg.end() :]
    needed: list[str] = []
    if _MOCKITO_USE.search(body) and "import static org.mockito.Mockito.*;" not in content:
        needed.append("import static org.mockito.Mockito.*;")
    if _ASSERTJ_USE.search(body) and "import static org.assertj.core.api.Assertions.*;" not in content:
        if "import static org.junit.jupiter.api.Assertions.*;" in content:
            for name in ("assertThat", "assertThatThrownBy", "assertThatCode", "assertThatExceptionOfType"):
                line = f"import static org.assertj.core.api.Assertions.{name};"
                if re.search(r"\b" + name + r"\(", body) and line not in content:
                    needed.append(line)
        else:
            needed.append("import static org.assertj.core.api.Assertions.*;")
    if "mockMvc" in body or "MockMvc" in body:
        if (
            _MOCKMVC_REQUEST_USE.search(body)
            and "import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;" not in content
        ):
            needed.append("import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;")
        if (
            _MOCKMVC_RESULT_USE.search(body)
            and "andExpect(" in body
            and "import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;" not in content
        ):
            needed.append("import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;")
    if not needed:
        return content
    return content[: pkg.end()] + "\n" + "\n".join(needed) + "\n" + content[pkg.end() :]


_JAVA_STRING_OR_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.S)
_TYPE_TOKEN = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\b")
_IMPORT_STMT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;", re.M)
_TYPE_DECL_NAME = re.compile(r"\b(?:class|interface|record|enum)\s+([A-Z]\w*)")


def project_type_index(sources: Mapping[str, str]) -> dict[str, str]:
    """由檔案路徑 + package 宣告建立「簡單名稱 → 完整類別名」索引；同名出現在多個套件時排除（避免猜錯）。"""
    seen: dict[str, set[str]] = {}
    for path, content in sources.items():
        if not path.endswith(".java"):
            continue
        pkg = _PACKAGE.search(content)
        if not pkg:
            continue
        simple = path.rsplit("/", 1)[-1][: -len(".java")]
        seen.setdefault(simple, set()).add(f"{pkg.group(1)}.{simple}")
    return {name: next(iter(fqcns)) for name, fqcns in seen.items() if len(fqcns) == 1}


def add_missing_project_imports(path: str, content: str, index: Mapping[str, str]) -> str:
    """模型最常見的編譯錯誤之一是忘了 import 專案內其他套件的型別（`cannot find symbol: class SaveProfilePort`）。
    對照專案型別索引，把有用到、不在同套件、也沒有被（明確或萬用）匯入的型別自動補上 import。"""
    if not path.endswith(".java") or not index:
        return content
    pkg = _PACKAGE.search(content)
    if not pkg:
        return content
    own_pkg = pkg.group(1)
    imported: set[str] = set()
    wildcard_pkgs: set[str] = set()
    for m in _IMPORT_STMT.finditer(content):
        fq = m.group(1)
        if fq.endswith(".*"):
            wildcard_pkgs.add(fq[:-2])
        else:
            imported.add(fq.rsplit(".", 1)[-1])
    declared = set(_TYPE_DECL_NAME.findall(content))
    code = _JAVA_STRING_OR_COMMENT.sub(" ", content)
    needed: list[str] = []
    for name in sorted(set(_TYPE_TOKEN.findall(code))):
        fqcn = index.get(name)
        if not fqcn or name in imported or name in declared:
            continue
        type_pkg = fqcn.rsplit(".", 1)[0]
        if type_pkg == own_pkg or type_pkg in wildcard_pkgs:
            continue
        needed.append(f"import {fqcn};")
    if not needed:
        return content
    line = _PACKAGE_LINE.search(content)
    if not line:
        return content
    return content[: line.end()] + "\n" + "\n".join(needed) + "\n" + content[line.end() :]


_MOCKITO_EXT = re.compile(r"@ExtendWith\(\s*MockitoExtension\.class\s*\)")


_MOCK_FIELD_USE = re.compile(r"^\s*@(?:Mock|InjectMocks|Spy|Captor)\b", re.M)


_INJECT_MOCKS_INIT = re.compile(
    r"(?P<head>@InjectMocks\s+(?:(?:private|protected|public)\s+)?)(?:final\s+)?"
    r"(?P<type>[\w.<>]+)\s+(?P<name>\w+)\s*=\s*new\s+(?P<impl>[\w.]+)\s*(?:<[^>]*>)?\s*\((?P<args>[^;]*)\)\s*;"
)


def fix_inject_mocks_initializers(path: str, content: str) -> str:
    """`@InjectMocks private GetProfileUseCase useCase = new GetProfileService(findProfilePort);`：
    欄位初始化在 Mockito 注入 @Mock 之前執行，service 拿到的是 null → 每個測試都 NPE，模型連續多輪改不好。
    改成 `@InjectMocks private GetProfileService useCase;` 讓 Mockito 以建構子注入自行建立。"""
    if "/src/test/" not in path or not path.endswith(".java") or "@Mock" not in content:
        return content

    def fix(m: re.Match) -> str:
        return f"{m.group('head')}{m.group('impl')} {m.group('name')};"

    content = _INJECT_MOCKS_INIT.sub(fix, content)
    # 同一個毛病的另一種寫法：沒有 @InjectMocks，直接 `private final XService s = new XService(mockPort);`
    mocks = set(_MOCK_FIELD_NAME.findall(content))
    if not mocks:
        return content

    def fix_plain(m: re.Match) -> str:
        args = {a.strip() for a in m.group("args").split(",")}
        if not (args & mocks):
            return m.group(0)
        return f"{m.group('indent')}@InjectMocks\n{m.group('indent')}private {m.group('impl')} {m.group('name')};"

    fixed = _PLAIN_MOCK_INIT.sub(fix_plain, content)
    if fixed != content and "import org.mockito.InjectMocks;" not in fixed:
        pkg = _PACKAGE_LINE.search(fixed)
        if pkg:
            fixed = fixed[: pkg.end()] + "\nimport org.mockito.InjectMocks;\n" + fixed[pkg.end() :]
    return fixed


_MOCK_FIELD_NAME = re.compile(r"@Mock\b[^;]*?\s(\w+)\s*;")
_PLAIN_MOCK_INIT = re.compile(
    r"^(?P<indent>[ \t]*)(?!@)(?:(?:private|protected|public)\s+)?(?:final\s+)?"
    r"(?P<type>[\w.<>]+)\s+(?P<name>\w+)\s*=\s*new\s+(?P<impl>[\w.]+)\s*(?:<[^>]*>)?\s*\((?P<args>[^;()]*)\)\s*;",
    re.M,
)


def ensure_mockito_extension(path: str, content: str) -> str:
    """用了 `@Mock` / `@InjectMocks` 卻沒有 `@ExtendWith(MockitoExtension.class)`（也沒呼叫 `openMocks`）時，
    所有 mock 都是 null → 整個測試類別 NPE。補上 extension 與 import。"""
    if not path.endswith(".java") or "/src/test/" not in path:
        return content
    if not _MOCK_FIELD_USE.search(content):
        return content
    if _MOCKITO_EXT.search(content) or "openMocks(" in content or "initMocks(" in content:
        return content
    if "@SpringBootTest" in content or "@WebMvcTest" in content or "@DataJpaTest" in content:
        return content  # Spring 測試用 @MockitoBean，不該混用 MockitoExtension
    decl = _CLASS_DECL.search(content)
    pkg = _PACKAGE_LINE.search(content)
    if not decl or not pkg:
        return content
    head = content[: decl.start()]
    lines = head.splitlines(keepends=True)
    insert_at = len(head)
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].lstrip().startswith("@"):
            insert_at = sum(len(x) for x in lines[:i])
        elif lines[i].strip():
            break
        elif insert_at != len(head):
            break  # 註解區塊之前的空白行：不再往前移
    content = content[:insert_at] + "@ExtendWith(MockitoExtension.class)\n" + content[insert_at:]
    imports = ""
    if "import org.junit.jupiter.api.extension.ExtendWith;" not in content:
        imports += "import org.junit.jupiter.api.extension.ExtendWith;\n"
    if "import org.mockito.junit.jupiter.MockitoExtension;" not in content:
        imports += "import org.mockito.junit.jupiter.MockitoExtension;\n"
    return content[: pkg.end()] + "\n" + imports + content[pkg.end() :]


def relax_mockito_strictness(path: str, content: str) -> str:
    """使用 `MockitoExtension` 的測試預設為 STRICT_STUBS：多餘的 stubbing 會讓測試以
    `UnnecessaryStubbingException` 失敗，本機模型反覆為此修測試。一律加上 `@MockitoSettings(strictness = LENIENT)`。"""
    if not path.endswith(".java") or "/src/test/" not in path:
        return content
    pkg = _PACKAGE_LINE.search(content)
    if not pkg:
        return content
    if "@MockitoSettings" in content:
        # 模型自己寫了註解卻常忘了 import（或只 import 其中一個）
        return _add_mockito_settings_imports(content, pkg.end())
    if not _MOCKITO_EXT.search(content):
        return content
    decl = _CLASS_DECL.search(content)
    if not decl:
        return content
    head = content[: decl.start()]
    lines = head.splitlines(keepends=True)
    insert_at = len(head)
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].lstrip().startswith("@"):
            insert_at = sum(len(x) for x in lines[:i])
        elif lines[i].strip():
            break
        elif insert_at != len(head):
            break  # 註解區塊之前的空白行：不再往前移
    content = content[:insert_at] + "@MockitoSettings(strictness = Strictness.LENIENT)\n" + content[insert_at:]
    return _add_mockito_settings_imports(content, pkg.end())


def _add_mockito_settings_imports(content: str, pkg_end: int) -> str:
    imports = ""
    if "import org.mockito.junit.jupiter.MockitoSettings;" not in content:
        imports += "import org.mockito.junit.jupiter.MockitoSettings;\n"
    if "Strictness." in content and "import org.mockito.quality.Strictness;" not in content:
        imports += "import org.mockito.quality.Strictness;\n"
    if not imports:
        return content
    return content[:pkg_end] + "\n" + imports + content[pkg_end:]


_GENERATED_VALUE_LINE = re.compile(r"^[ \t]*@GeneratedValue(?:\([^)]*\))?[ \t]*\n", re.M)
_GENERATED_VALUE_INLINE = re.compile(r"@GeneratedValue(?:\([^)]*\))?\s*")
_GENERATION_IMPORTS = re.compile(r"^\s*import\s+jakarta\.persistence\.(?:GeneratedValue|GenerationType)\s*;\s*\n", re.M)


def strip_generated_value(path: str, content: str) -> str:
    """本專案的識別碼由 domain 產生（值物件內含 UUID），JPA Entity 若再標 `@GeneratedValue`，
    Hibernate 會把帶有 id 的新實體當成 detached 更新 → `ObjectOptimisticLockingFailureException`。
    一律移除 `@GeneratedValue`（移除後 `save()` 會先 SELECT 再 INSERT，行為正確）。"""
    if not path.endswith(".java") or not path.startswith("adapter-persistence/src/main/") or "@Entity" not in content:
        return content
    if "@GeneratedValue" not in content:
        return content
    content = _GENERATED_VALUE_LINE.sub("", content)
    content = _GENERATED_VALUE_INLINE.sub("", content)
    if "GenerationType" not in content.replace("import jakarta.persistence.GenerationType;", ""):
        content = _GENERATION_IMPORTS.sub("", content)
    else:
        content = re.sub(r"^\s*import\s+jakarta\.persistence\.GeneratedValue\s*;\s*\n", "", content, flags=re.M)
    return content


_WEBMVC_TARGET = re.compile(r"@WebMvcTest\(\s*(?:controllers\s*=\s*)?\{?\s*(?P<name>\w+)\.class")
_MOCK_FIELD = re.compile(r"@(?:MockitoBean|MockBean)\s+(?:private\s+|protected\s+|public\s+)?(?P<type>\w+)\s+\w+\s*;")
_CTOR_PARAM_TYPES = re.compile(r"\b(?P<type>[A-Z]\w*(?:UseCase|Port|Service))\b\s+\w+\s*[,)]")
_CLASS_BODY_OPEN = re.compile(r"\bclass\s+\w+[^{]*\{")


def webmvc_controller_name(test_content: str) -> str | None:
    m = _WEBMVC_TARGET.search(test_content)
    return m.group("name") if m else None


def complete_webmvc_mocks(test_content: str, controller_content: str) -> str:
    """`@WebMvcTest(XController.class)` 的測試必須為 controller 建構子注入的每個 use case 提供 `@MockitoBean`，
    否則 context 載入失敗（`No qualifying bean of type ...UseCase`）。缺的自動補上。"""
    needed = []
    present = set(_MOCK_FIELD.findall(test_content))
    for t in dict.fromkeys(_CTOR_PARAM_TYPES.findall(controller_content)):
        if t not in present and "@MockitoBean" in test_content or t not in present:
            needed.append(t)
    needed = [t for t in needed if t not in present]
    if not needed:
        return test_content
    body = _CLASS_BODY_OPEN.search(test_content)
    if not body:
        return test_content
    fields = "".join(f"\n    @MockitoBean\n    private {t} {t[0].lower() + t[1:]};\n" for t in needed)
    content = test_content[: body.end()] + fields + test_content[body.end() :]
    if "import org.springframework.test.context.bean.override.mockito.MockitoBean;" not in content:
        pkg = _PACKAGE_LINE.search(content)
        if pkg:
            content = (
                content[: pkg.end()]
                + "\nimport org.springframework.test.context.bean.override.mockito.MockitoBean;\n"
                + content[pkg.end() :]
            )
    return content


def _statement_end(content: str, start: int) -> int:
    """從 start 開始，回傳第一個位於括號深度 0 的 `;` 的索引（忽略字串常值）。找不到回傳 -1。"""
    depth = 0
    i = start
    in_str = False
    while i < len(content):
        ch = content[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == ";" and depth == 0:
            return i
        i += 1
    return -1


def _enclosing_block_end(content: str, pos: int) -> int:
    """回傳包住 pos 的最內層 `{...}` 區塊之右大括號索引（忽略字串常值）。找不到回傳 -1。"""
    depth = 0
    i = pos
    in_str = False
    while i < len(content):
        ch = content[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            if depth == 0:
                return i
            depth -= 1
        i += 1
    return -1


_CAUSE_ASSERTION = re.compile(
    r"\s*\.has(?:Root)?Cause(?:InstanceOf|ExactlyInstanceOf)?\(\s*[\w.]+\.class\s*\)"
    r"|\s*\.has(?:Root)?Cause\(\s*\w+\s*\)"
)


def strip_cause_assertions(path: str, content: str) -> str:
    """adapter 測試常寫 `assertThatThrownBy(...).isInstanceOf(DuplicateEmailException.class)
    .hasCauseInstanceOf(DataIntegrityViolationException.class)`，但 domain 例外多半只有 (String) 建構子、
    Port 轉換時不會帶 cause → `but current throwable has no cause`，本機模型會連續多輪改不好。
    cause 是實作細節、規格沒有要求，直接移除這段鏈式斷言。"""
    if "/src/test/" not in path or not path.endswith(".java") or "assertThatThrownBy" not in content:
        return content
    return _CAUSE_ASSERTION.sub("", content)


_THROWN_ASSERTION = re.compile(r"^[ \t]*assertThatThrownBy\(", re.M)
_ABORTED_NOTE = "// 唯一約束違反後 PostgreSQL 交易已中止，後續查詢/斷言已由 agent 移除\n"


def truncate_after_thrown_assertion(path: str, content: str) -> str:
    """`@DataJpaTest`（PostgreSQL）中，`assertThatThrownBy(...)` 之後同一方法內的任何語句都會因
    `current transaction is aborted` 失敗；模型即使寫了「這必須是最後一行」的註解仍會接著查詢。把這些語句移除。"""
    if not path.startswith("adapter-persistence/src/test/") or "@DataJpaTest" not in content:
        return content
    out = content
    offset = 0
    for m in list(_THROWN_ASSERTION.finditer(content)):
        start = m.start() + offset
        end = _statement_end(out, start)
        if end < 0:
            continue
        block_end = _enclosing_block_end(out, end + 1)
        if block_end < 0:
            continue
        tail = out[end + 1 : block_end]
        # 只保留空白與註解；若有其他語句則移除
        stripped = re.sub(r"//[^\n]*|/\*.*?\*/", "", tail, flags=re.S).strip()
        if not stripped:
            continue
        indent_match = re.match(r"[ \t]*", out[m.start() + offset : m.start() + offset + 40])
        indent = indent_match.group(0) if indent_match else ""
        replacement = "\n" + indent + _ABORTED_NOTE + indent[:-4] if len(indent) >= 4 else "\n" + _ABORTED_NOTE
        out = out[: end + 1] + replacement + out[block_end:]
        offset += len(replacement) - len(tail)
    return out


# Gherkin 步驟裡不該出現 `{xxx}`：那是 Cucumber Expression（步驟定義）或 URI 模板的語法；
# Scenario Outline 的變數要用 `<xxx>`
_GHERKIN_PLACEHOLDER = re.compile(r"\{[A-Za-z_]\w*\}")
_OUTLINE_VAR = re.compile(r"<[^<>\s|\"/]{1,40}>")  # 含中文欄位名（<姓名>、<電子郵件>）
_GHERKIN_BARE_UUID = re.compile(
    r'(?<!")\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b(?!")'
)


def gherkin_violations(files) -> list[str]:
    """`.feature` 檔的步驟必須是具體文字：不可含 Cucumber Expressions 的參數佔位
    （`{string}`、`{int}` 是步驟定義的語法），且 UUID 等參數值要用雙引號包住
    （否則 Cucumber 會把 `8901` 之類的片段當成 `{int}` 擷取，步驟定義無法逐字對應）。"""
    problems: list[str] = []
    for f in files:
        if not f.path.endswith(".feature"):
            continue
        in_doc = False
        in_outline = False
        for i, line in enumerate(f.content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(('"""', "```")):
                in_doc = not in_doc
                continue
            if in_doc or not stripped or stripped.startswith(("#", "|", "@")):
                continue
            if _GHERKIN_BLOCK_START.match(line):
                in_outline = stripped.startswith(("場景大綱", "劇本大綱"))
                continue
            if not _GHERKIN_STEP_START.match(line):
                continue
            if not in_outline and _OUTLINE_VAR.search(stripped):
                problems.append(
                    f"{f.path}:{i}：`<欄位名>` 只能用在 `場景大綱:`（搭配 `例子:` 表格）；一般 `場景:` 請寫具體值"
                )
                continue
            if _GHERKIN_PLACEHOLDER.search(stripped):
                problems.append(
                    f"{f.path}:{i}：步驟文字不可含 `{{...}}` 佔位"
                    "（`{string}`/`{int}` 是步驟定義語法、`{id}` 不會被替換）；"
                    "Scenario Outline 的變數請用 `<欄位名>`，其他請寫具體值並用雙引號包住"
                )
            elif _GHERKIN_BARE_UUID.search(re.sub(r'"[^"]*"', '""', stripped)):
                problems.append(
                    f'{f.path}:{i}：UUID 參數必須用雙引號包住（例如 "550e8400-..."），步驟定義才能以 {{string}} 擷取'
                )
    return problems


_JPA_ANNOTATIONS = (
    "Entity",
    "Table",
    "Id",
    "Column",
    "Transient",
    "Version",
    "GeneratedValue",
    "GenerationType",
    "PostLoad",
    "PostPersist",
    "PrePersist",
    "PreUpdate",
    "Enumerated",
    "EnumType",
    "Embedded",
    "Embeddable",
    "EmbeddedId",
    "OneToMany",
    "ManyToOne",
    "OneToOne",
    "ManyToMany",
    "JoinColumn",
    "Lob",
    "Index",
    "UniqueConstraint",
    "MappedSuperclass",
    "Temporal",
    "TemporalType",
    "Convert",
    "Converter",
    "AttributeConverter",
)


def ensure_jakarta_imports(path: str, content: str) -> str:
    """JPA 類別若用了 `@Transient`、`@PostLoad` 等註解卻沒 import（`cannot find symbol: class Transient`），
    補上 `jakarta.persistence.*` 的對應 import。
    只處理已經 import 任一 jakarta.persistence 型別的檔案（確定是 JPA 類別）。"""
    if not path.endswith(".java") or "jakarta.persistence." not in content:
        return content
    if "import jakarta.persistence.*;" in content:
        return content
    pkg = _PACKAGE_LINE.search(content)
    if not pkg:
        return content
    body = _JAVA_STRING_OR_COMMENT.sub(" ", content[pkg.end() :])
    needed = []
    for name in _JPA_ANNOTATIONS:
        used = re.search(r"@" + name + r"\b", body) or re.search(r"\b" + name + r"\.", body)
        if used and f"import jakarta.persistence.{name};" not in content:
            needed.append(f"import jakarta.persistence.{name};")
    if not needed:
        return content
    return content[: pkg.end()] + "\n" + "\n".join(needed) + "\n" + content[pkg.end() :]


_RECORD_DECL = re.compile(r"\brecord\s+(?P<name>\w+)\s*\((?P<components>[^)]*)\)")
_THIS_ASSIGN = re.compile(r"\bthis\.(\w+)\s*=(?!=)")
_THIS_CALL = re.compile(r"\bthis\s*\((?P<args>[^;]*)\)\s*;")


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def fix_record_compact_constructors(path: str, content: str) -> str:
    """record 的精簡建構子（`public Name {`，無參數列）裡不可寫 `this.x = ...` 也不可呼叫 `this(...)`
    （`cannot assign a value to final variable` / `call to this must be first statement`），
    要直接對元件參數賦值 `x = ...`。模型極常犯這個錯且多輪修不好。"""
    if not path.endswith(".java") or "record " not in content:
        return content
    out = content
    for m in list(_RECORD_DECL.finditer(content)):
        name = m.group("name")
        components = [c.split()[-1] for c in _split_top_level(m.group("components")) if c.split()]
        compact = re.compile(r"\b(?:public\s+|protected\s+|private\s+)?" + re.escape(name) + r"\s*\{")
        pos = 0
        while True:
            cm = compact.search(out, pos)
            if not cm:
                break
            body_start = cm.end()
            body_end = _enclosing_block_end(out, body_start)
            if body_end < 0:
                break
            body = out[body_start:body_end]
            fixed = _THIS_ASSIGN.sub(r"\1 =", body)

            comps = tuple(components)
            fixed = _THIS_CALL.sub(functools.partial(_expand_this_call, components=comps), fixed)
            out = out[:body_start] + fixed + out[body_end:]
            pos = body_start + len(fixed)
    return out


def _expand_this_call(tm: re.Match, components: tuple[str, ...]) -> str:
    args = _split_top_level(tm.group("args"))
    if len(args) != len(components):
        return tm.group(0)
    return " ".join(f"{comp} = {arg};" for comp, arg in zip(components, args, strict=True))


_NEW_RESPONSE_ENTITY = re.compile(r"new\s+ResponseEntity<[^>]*>\s*\(")
_LOOKS_LIKE_STATUS = re.compile(r"getStatusCode\(\)|HttpStatus(?:Code)?\.|\bstatus\b", re.I)
_LOOKS_LIKE_HEADERS = re.compile(r"getHeaders\(\)|HttpHeaders|\bheaders\b", re.I)


def fix_response_entity_arg_order(path: str, content: str) -> str:
    """`new ResponseEntity<>(body, status, headers)` 不存在（正確順序是 body, headers, status）→
    `cannot infer type arguments for ResponseEntity<>`。偵測到第二個參數像狀態碼、第三個像標頭時交換。"""
    if not path.endswith(".java") or "new ResponseEntity<" not in content:
        return content
    out = content
    pos = 0
    while True:
        m = _NEW_RESPONSE_ENTITY.search(out, pos)
        if not m:
            break
        start = m.end()
        depth = 1
        i = start
        while i < len(out) and depth:
            if out[i] == "(":
                depth += 1
            elif out[i] == ")":
                depth -= 1
            i += 1
        if depth:
            break
        args = _split_top_level(out[start : i - 1])
        if len(args) == 3 and _LOOKS_LIKE_STATUS.search(args[1]) and _LOOKS_LIKE_HEADERS.search(args[2]):
            replacement = f"{args[0]}, {args[2]}, {args[1]}"
            out = out[:start] + replacement + out[i - 1 :]
            pos = start + len(replacement)
        else:
            pos = i
    return out


_STEP_ANNOTATION_LINE = re.compile(
    r"^[ \t]*@(?:假如|假設|假定|當|那麼|並且|而且|同時|但是|Given|When|Then|And|But)"
    r'\(\s*"(?P<expr>(?:\\.|[^"\\])*)"\s*\)[ \t]*\n',
    re.M,
)


def dedupe_step_definitions(path: str, content: str) -> str:
    """同一個步驟類別裡兩個方法用了相同的步驟文字會讓 Cucumber 拋 DuplicateStepDefinitionException，
    進而讓**所有**步驟都變成 undefined。保留第一個定義，移除後續重複的註解與整個方法。"""
    if not path.endswith(".java") or "/src/test/" not in path or not is_cucumber_glue(content):
        return content
    seen: set[str] = set()
    out = content
    pos = 0
    while True:
        m = _STEP_ANNOTATION_LINE.search(out, pos)
        if not m:
            break
        expr = m.group("expr")
        if expr not in seen:
            seen.add(expr)
            pos = m.end()
            continue
        # 重複：刪除從這個註解（含其後緊接的其他註解行）到方法結尾
        start = m.start()
        cursor = m.end()
        while True:
            nxt = re.match(r"[ \t]*@\w+(?:\([^)]*\))?[ \t]*\n", out[cursor:])
            if not nxt:
                break
            cursor += nxt.end()
        brace = out.find("{", cursor)
        semicolon = out.find(";", cursor)
        if brace < 0 or (0 <= semicolon < brace):
            pos = m.end()
            continue
        end = _enclosing_block_end(out, brace + 1)
        if end < 0:
            pos = m.end()
            continue
        # 吃掉結尾大括號後的換行
        end += 1
        if out[end : end + 1] == "\n":
            end += 1
        out = out[:start] + out[end:]
        pos = start
    return out


def _synthetic_value(name: str, ordinal: int) -> str:
    lowered = name.lower()
    if "id" in lowered or "uuid" in lowered or "識別" in name or "編號" in name:
        return f"00000000-0000-4000-8000-{ordinal:012d}"
    if "mail" in lowered or "郵件" in name or "信箱" in name:
        return f"user{ordinal}@example.com"
    if "name" in lowered or "名" in name:
        return f"使用者{ordinal}"
    return f"{name}{ordinal}"


_PLAIN_SCENARIO_LINE = re.compile(r"^(?P<indent>\s*)(?P<kw>場景|劇本)\s*[:：]")
_EXAMPLES_LINE = re.compile(r"^\s*例子\s*[:：]")


_TRAILING_STEP_NOTE = re.compile(r"\s*[（(][^（）()\"]*[）)]\s*$")


def _strip_trailing_step_notes(content: str) -> str:
    """步驟尾端的括號說明（`…"/profiles/99999"（假設該 ID 不存在）`）會讓步驟定義永遠對不上 → undefined。
    只處理步驤行、且括號內沒有引號（避免動到真正的參數）。"""
    out: list[str] = []
    in_doc = False
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith(('"""', "```")):
            in_doc = not in_doc
        elif not in_doc and _GHERKIN_STEP_START.match(line) and _TRAILING_STEP_NOTE.search(line.rstrip("\n")):
            body = line.rstrip("\n")
            line = _TRAILING_STEP_NOTE.sub("", body) + ("\n" if line.endswith("\n") else "")
        out.append(line)
    return "".join(out)


_EMAIL_IN_FEATURE = re.compile(r"\b([A-Za-z0-9_][\w.+-]*)@([\w-]+(?:\.[\w-]+)+)\b")
_SCENARIO_HEADER = re.compile(r"^\s*(?:場景大綱|劇本大綱|場景|劇本)\s*[:：]")


def _dedupe_emails_across_scenarios(content: str) -> str:
    """所有場景共用同一個資料庫且不會清空：場景 A 用 POST 建了 `user@example.com`，場景 B 再把別筆資料 PUT 成同一個
    email 就會撞唯一約束 → 409（attempt 76 卡在這一步）。同一個 email 出現在多個場景時，後面的場景改用
    `sN.<local>@<domain>`；同一場景內保持一致（重複 email 的場景本來就該在場景內重複）。"""
    lines = content.splitlines(keepends=True)
    seen_before: set[str] = set()  # 先前場景用過的 email
    current: dict[str, str] = {}  # 本場景的替換表
    current_emails: set[str] = set()
    scenario_no = 0
    out: list[str] = []

    def sub(m: re.Match) -> str:
        email = m.group(0)
        if email in current:
            return current[email]
        if email in seen_before:
            current[email] = f"s{scenario_no}.{m.group(1)}@{m.group(2)}"
        else:
            current[email] = email
        current_emails.add(email)
        return current[email]

    for line in lines:
        if _SCENARIO_HEADER.match(line):
            seen_before |= current_emails
            current, current_emails = {}, set()
            scenario_no += 1
            out.append(line)
            continue
        out.append(_EMAIL_IN_FEATURE.sub(sub, line) if "@" in line else line)
    return "".join(out)


def _promote_scenarios_with_examples(content: str) -> str:
    """一般 `場景:` 底下接了 `例子:` 表格是無效 Gherkin（整個 .feature 解析失敗、所有場景 fail）；
    模型常這樣寫（步驟裡用 `<姓名>` 又忘了寫 `場景大綱`）。有 `例子:` 的場景一律升格為 `場景大綱:`。"""
    lines = content.splitlines(keepends=True)
    header_idx: int | None = None
    for i, line in enumerate(lines):
        # 例子: 本身也是 _GHERKIN_BLOCK_START 的一種，必須先判斷
        if _EXAMPLES_LINE.match(line):
            if header_idx is not None:
                hdr = _PLAIN_SCENARIO_LINE.match(lines[header_idx])
                if hdr is not None:
                    lines[header_idx] = f"{hdr.group('indent')}場景大綱:" + lines[header_idx][hdr.end() :]
            header_idx = None
            continue
        if _GHERKIN_BLOCK_START.match(line):
            header_idx = i if _PLAIN_SCENARIO_LINE.match(line) else None
    return "".join(lines)


def _substitute_vars_in_plain_scenarios(content: str) -> str:
    """一般 `場景:`（非大綱）裡的 `<id>`、`<email>` 不會被 Gherkin 替換，步驟文字會含有字面 `<id>`。
    在同一場景內把每個變數換成固定的合成值（id → 固定 UUID、email → userN@example.com），Given/When/Then 之間仍一致。"""
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    in_outline = False
    in_doc = False
    mapping: dict[str, str] = {}
    counter = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(('"""', "```")):
            in_doc = not in_doc
            out.append(line)
            continue
        if not in_doc and _GHERKIN_BLOCK_START.match(line):
            in_outline = stripped.startswith(("場景大綱", "劇本大綱"))
            mapping = {}
            counter += 1
            out.append(line)
            continue
        if in_outline or stripped.startswith(("#", "|", "@")):
            out.append(line)
            continue
        if _OUTLINE_VAR.search(line):
            line = _OUTLINE_VAR.sub(functools.partial(_replace_outline_var, mapping=mapping, scenario=counter), line)
        out.append(line)
    return "".join(out)


_SYMBOLIC_ID = re.compile(r"\b((?:[A-Za-z]+-)+id)\b")
# `"/profiles/99999"`、`ID 為 "99999"`：純數字 id 一樣會讓 UUID 解析失敗 → 400
_NUMERIC_ID = re.compile(r'(?<=/)(\d{3,})(?=")|(?<=")(\d{3,})(?=")')


def _replace_symbolic_ids(content: str) -> str:
    """模型常在 .feature 用 `update-id`、`non-existent-id` 這類象徵性 id；Controller 以 `UUID.fromString` 解析時會丟
    IllegalArgumentException → 400（規格要 404），前置步驟也無法以這種 id 建資料。
    把每個不同的象徵性 id 換成固定的合法 UUID（同一檔案內一致，前置步驟與請求路徑仍對得上）。"""
    mapping: dict[str, str] = {}

    def sub(m: re.Match) -> str:
        token = next(g for g in m.groups() if g)
        if token not in mapping:
            mapping[token] = f"00000000-0000-4000-8000-{9000 + len(mapping) + 1:012d}"
        return mapping[token]

    content = _SYMBOLIC_ID.sub(sub, content)
    return _NUMERIC_ID.sub(sub, content)


def _replace_outline_var(m: re.Match, mapping: dict[str, str], scenario: int) -> str:
    name = m.group(0)[1:-1]
    if name not in mapping:
        mapping[name] = _synthetic_value(name, scenario * 10 + len(mapping) + 1)
    return mapping[name]


_NESTED_TYPE_DECL = re.compile(
    r"^[ \t]+(?:public\s+|protected\s+|private\s+)?(?:static\s+)?(?:final\s+)?"
    r"(?:class|record|interface|enum)\s+(?P<name>[A-Z]\w*)",
    re.M,
)


def shadowing_violations(files, index: Mapping[str, str]) -> list[str]:
    """介面 / 服務裡再宣告一個與專案既有型別同名的巢狀型別（例如在 `GetProfileUseCase` 裡又定義 `record Profile`），
    會讓實作與介面的 `Profile` 變成兩個不同型別（`return type Optional<...Service.Profile> is not compatible`）。
    有同名專案型別存在時直接拒絕，要求 import 既有型別。"""
    problems: list[str] = []
    for f in files:
        if not f.path.endswith(".java") or "/src/main/" not in f.path:
            continue
        own = f.path.rsplit("/", 1)[-1][:-5]
        for m in _NESTED_TYPE_DECL.finditer(_JAVA_STRING_OR_COMMENT.sub(" ", f.content)):
            name = m.group("name")
            fqcn = index.get(name)
            if fqcn and name != own and not fqcn.endswith(f".{own}.{name}"):
                problems.append(
                    f"{f.path}：巢狀型別 `{name}` 與專案既有型別 `{fqcn}` 同名，"
                    f"請改為 `import {fqcn};` 直接使用，不要重新定義"
                )
    return problems


_ADAPTER_SUBPKG = re.compile(r"^(?P<base>[\w.]+\.adapter\.(?:web|persistence))\.(?P<sub>[\w.]+)$")
_ADAPTER_PATH = re.compile(
    r"^(?P<module>adapter-(?:web|persistence))/src/(?P<kind>main|test)/java/(?P<rest>.+)/(?P<file>[^/]+\.java)$"
)


def flatten_adapter_subpackages(files: list) -> list:
    """adapter-web / adapter-persistence 規定只有一層套件；模型極常寫 `...adapter.web.dto`、
    `...adapter.persistence.converter`。與其拒絕（每次重試都要花一輪），直接把套件宣告攤平、
    檔案搬到正確目錄，並修正輸出中所有對應的 import。"""
    renames: dict[str, str] = {}
    flattened = []
    for f in files:
        m = _ADAPTER_PATH.match(f.path)
        pkg = _PACKAGE.search(f.content) if m else None
        sub = _ADAPTER_SUBPKG.match(pkg.group(1)) if pkg else None
        if not (m and pkg and sub):
            flattened.append(f)
            continue
        base = sub.group("base")
        renames[pkg.group(1)] = base
        content = f.content[: pkg.start(1)] + base + f.content[pkg.end(1) :]
        new_path = f"{m.group('module')}/src/{m.group('kind')}/java/{base.replace('.', '/')}/{m.group('file')}"
        flattened.append(type(f)(path=new_path, content=content))
    if not renames:
        return files
    result = []
    for f in flattened:
        content = f.content
        if f.path.endswith(".java"):
            for old, new in renames.items():
                content = content.replace(f"import {old}.", f"import {new}.").replace(
                    f"import static {old}.", f"import static {new}."
                )
                content = content.replace(f"{old}.", f"{new}.")
        result.append(type(f)(path=f.path, content=content) if content != f.content else f)
    return result


_REQUEST_BODY_WITHOUT_VALID = re.compile(r"(?<![\w@])@RequestBody\b")
_VALID_BEFORE_BODY = re.compile(r"@Valid(?:ated)?\s+@RequestBody")


def ensure_valid_on_request_body(path: str, content: str) -> str:
    """Controller 的 `@RequestBody` DTO 若沒有 `@Valid`，Bean Validation（`@NotBlank`/`@Email`）不會執行，
    無效輸入會回 201 而不是 400。一律在 `@RequestBody` 前補 `@Valid` 並 import。"""
    if not path.endswith(".java") or not path.startswith("adapter-web/src/main/") or "@RequestBody" not in content:
        return content
    pieces = []
    last = 0
    changed = False
    for m in _REQUEST_BODY_WITHOUT_VALID.finditer(content):
        before = content[max(0, m.start() - 12) : m.start()]
        if re.search(r"@Valid\s*$", before):
            continue
        pieces.append(content[last : m.start()])
        pieces.append("@Valid @RequestBody")
        last = m.end()
        changed = True
    if not changed:
        return content
    pieces.append(content[last:])
    content = "".join(pieces)
    if "import jakarta.validation.Valid;" not in content:
        pkg = _PACKAGE_LINE.search(content)
        if pkg:
            content = content[: pkg.end()] + "\nimport jakarta.validation.Valid;\n" + content[pkg.end() :]
    return content


_IMPORT_ANNOTATION = re.compile(r"@Import\(\s*(?P<body>\{[^}]*\}|[^)]*)\s*\)")


def _strip_repository_imports(content: str) -> str:
    """`@Import({JpaSaveProfilePort.class, ProfileRepository.class})`：Spring Data 的 repository 是介面，
    放進 @Import 會 `Failed to instantiate ... Specified class is an interface`；
    @DataJpaTest 本來就會註冊 repository，直接移除。"""

    def fix(m: re.Match) -> str:
        body = m.group("body").strip()
        inner = body[1:-1] if body.startswith("{") else body
        kept = [e.strip() for e in inner.split(",") if e.strip() and not re.search(r"\w*Repository\.class$", e.strip())]
        if not kept:
            return ""
        if len(kept) == 1:
            return f"@Import({kept[0]})"
        return "@Import({" + ", ".join(kept) + "})"

    return _IMPORT_ANNOTATION.sub(fix, content)
