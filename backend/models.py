from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# The old OnCall pipeline lane was removed; the gateway now only has the RAG lane.
# The live operational path is /api/assistant -> RouterService -> experts.
AgentMode = Literal["auto", "rag"]
ResolvedAgentRoute = Literal["rag"]


class AgentStreamRequest(BaseModel):
    session_id: str = Field(default="default", min_length=1)
    message: str = Field(min_length=1)
    mode: AgentMode = "auto"
