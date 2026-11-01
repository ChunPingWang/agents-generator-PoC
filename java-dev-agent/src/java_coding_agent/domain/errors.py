class DomainError(Exception):
    """所有領域錯誤的基底。"""


class ValidationError(DomainError):
    """設定或輸入值不合法。"""


class PipelineError(DomainError):
    """SDD 流程在某階段失敗。"""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"[{stage}] {message}")
        self.stage = stage
        self.message = message


class LlmOutputError(DomainError):
    """LLM 輸出無法解析成預期格式。"""
