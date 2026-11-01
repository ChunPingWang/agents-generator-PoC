"""BuildRunnerPort 的 Gradle 實作：在隔離 workspace 中執行 `./gradlew test`。

若 workspace 尚無 wrapper，先用主機的 `gradle wrapper` 產生（骨架不含二進位 jar）。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from java_coding_agent.domain.models import BuildResult

log = logging.getLogger(__name__)


class GradleBuildRunner:
    def __init__(
        self,
        *,
        java_home: str | None = None,
        gradle_bin: str = "gradle",
        gradle_version: str = "9.8.2",
        timeout_seconds: int = 1800,
        extra_args: tuple[str, ...] = (),
    ) -> None:
        self._java_home = java_home
        self._gradle_bin = gradle_bin
        self._gradle_version = gradle_version
        self._timeout = timeout_seconds
        self._extra_args = extra_args

    def run_tests(self, workspace: Path, *, test_filter: str | None = None) -> BuildResult:
        workspace = workspace.resolve()  # cwd 會切到 workspace，路徑必須是絕對路徑
        self._ensure_wrapper(workspace)
        cmd = [str(workspace / "gradlew"), "test", "--console=plain", "--stacktrace", *self._extra_args]
        if test_filter:
            cmd += ["--tests", test_filter]
        return self._run(cmd, workspace)

    def _ensure_wrapper(self, workspace: Path) -> None:
        if (workspace / "gradlew").exists():
            return
        gradle = shutil.which(self._gradle_bin) or self._gradle_bin
        log.info("workspace 無 gradlew，以 %s 產生 wrapper %s", gradle, self._gradle_version)
        result = self._run([gradle, "wrapper", "--gradle-version", self._gradle_version, "-q"], workspace)
        if not result.success:
            raise RuntimeError(f"無法產生 Gradle wrapper：{result.summary()}")

    def _run(self, cmd: list[str], cwd: Path) -> BuildResult:
        env = dict(os.environ)
        if self._java_home:
            env["JAVA_HOME"] = self._java_home
            env["PATH"] = f"{self._java_home}/bin:{env.get('PATH', '')}"
        started = time.monotonic()
        try:
            proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=self._timeout)
        except subprocess.TimeoutExpired as exc:
            return BuildResult(
                success=False,
                exit_code=124,
                stdout=(exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
                stderr=f"Gradle 執行超過 {self._timeout} 秒",
                duration_seconds=time.monotonic() - started,
            )
        return BuildResult(
            success=proc.returncode == 0,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_seconds=time.monotonic() - started,
        )
