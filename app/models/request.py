""" """  """ """"""请求数据模型

定义 API 请求的 Pydantic 模型
"""

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    """对话请求"""

    id: str = Field(..., description="会话 ID", alias="Id")
    question: str = Field(..., description="用户问题", alias="Question")
    attachment_ids: list[str] = Field(default_factory=list, description="附件 ID 列表", alias="AttachmentIds")
    checkpoint_replay: bool | None = Field(
        default=None,
        description="本次请求是否激进恢复 checkpoint：True 强制重放非白名单工具，False 强制保守收口，None 沿用 config.harness_checkpoint_replay",
        alias="CheckpointReplay",
    )

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "Id": "session-123",
                "Question": "什么是向量数据库？",
                "AttachmentIds": ["file_123"]
            }
        }
    )
