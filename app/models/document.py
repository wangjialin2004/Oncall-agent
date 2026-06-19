"""Document-related data models."""

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


@dataclass
class RetrievedDocument:
    page_content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class DocumentChunk(BaseModel):
    """Document chunk model."""

    content: str = Field(..., description="Chunk content")
    start_index: int = Field(..., description="Chunk start position in the source document")
    end_index: int = Field(..., description="Chunk end position in the source document")
    chunk_index: int = Field(..., description="Chunk index, starting from 0")
    title: str | None = Field(None, description="Section title for the chunk")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "content": "This is a sample document chunk.",
                "start_index": 0,
                "end_index": 100,
                "chunk_index": 0,
                "title": "Section 1",
            }
        }
    )
