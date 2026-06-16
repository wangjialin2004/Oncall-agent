from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AgentMode = Literal["auto", "rag", "oncall"]
ResolvedAgentRoute = Literal["rag", "oncall"]


class AgentStreamRequest(BaseModel):
    session_id: str = Field(default="default", min_length=1)
    message: str = Field(min_length=1)
    mode: AgentMode = "auto"
