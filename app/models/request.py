(
    """ """
    """ """
    """请求数据模型

定义 API 请求的 Pydantic 模型
"""
)

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    """对话请求"""

    id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
        description="会话 ID",
        alias="Id",
    )
    question: str = Field(
        ..., min_length=1, max_length=20000, description="用户问题", alias="Question"
    )
    attachment_ids: list[str] = Field(
        default_factory=list, max_length=20, description="附件 ID 列表", alias="AttachmentIds"
    )
    checkpoint_replay: bool | None = Field(
        default=None,
        description="本次请求是否激进恢复 checkpoint：True 强制重放非白名单工具，False 强制保守收口，None 沿用 config.harness_checkpoint_replay",
        alias="CheckpointReplay",
    )
    # M3 W9 eval-only hooks (ignored in normal product traffic unless set).
    simulate: str | None = Field(
        default=None,
        max_length=128,
        description="评测/调试故障注入标签，如 prometheus_unavailable_with_delegation_off",
        alias="Simulate",
    )
    prefer_parallel: bool | None = Field(
        default=None,
        description="评测提示：跨域优先 seed delegate_parallel",
        alias="PreferParallel",
    )

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        json_schema_extra={
            "example": {
                "Id": "session-123",
                "Question": "什么是向量数据库？",
                "AttachmentIds": ["file_123"],
            }
        },
    )
