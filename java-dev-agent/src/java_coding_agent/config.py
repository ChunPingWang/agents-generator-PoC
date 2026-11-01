"""應用程式設定（pydantic-settings）。金鑰只能來自環境變數或 .env。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- 一般 ---
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    data_dir: Path = Path("workspace/data")
    workspace_root: Path = Path("workspace/runs")
    database_url: str = ""
    """空字串時使用 data_dir 下的 SQLite；docker-compose 環境請設定 PostgreSQL URL。"""

    # --- LLM 預設值（可於執行期由對話修改，存於 DB） ---
    llm_provider: str = "ollama"
    llm_base_url: str = "http://localhost:11434"
    llm_model: str = "qwen3.6:35b-a3b-coding-mxfp8"
    llm_temperature: float = 0.0
    llm_context_length: int = 32768
    llm_request_timeout_seconds: float = 900.0
    """單次 LLM 呼叫的硬性上限（秒）；超時視為無效輸出並重試。"""
    llm_max_output_tokens: int = 24000
    llm_thinking: bool = False
    """Ollama 推理模型是否啟用 thinking；思考 token 會吃掉輸出額度，產碼時預設關閉。"""

    # --- 目標 repo 預設值 ---
    default_repo_url: str = "https://github.com/ChunPingWang/ollama-agent-outcome.git"
    default_repo_base_branch: str = "main"
    github_token: str | None = Field(default=None, alias="GITHUB_TOKEN")

    # --- Langfuse ---
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "http://localhost:3000"
    prompt_store: str = "auto"
    """auto：Langfuse 可用則用 Langfuse，否則檔案；file / langfuse 強制指定。"""

    # --- Java 建置 ---
    java_home: str | None = "/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"
    gradle_bin: str = "gradle"
    gradle_version: str = "9.8.2"
    gradle_timeout_seconds: int = 1800

    # --- SDD 流程 ---
    pipeline_max_fix_attempts: int = 10
    pipeline_max_parse_retries: int = 2
    pipeline_strict_red: bool = False

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.data_dir / 'agent.db').as_posix()}"
