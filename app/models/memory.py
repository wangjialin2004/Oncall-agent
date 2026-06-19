from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MemoryFeedbackRequest(BaseModel):
    session_id: str = Field(default="")
    user_message: str
    assistant_answer: str = Field(default="")
    user_accepted: bool = False
    # "strong": explicit adopt/correct (high confidence). "weak": passive acceptance
    # when the user moved on without correcting (low confidence, promoted by later hits).
    acceptance_level: Literal["strong", "weak"] = "strong"
    actual_root_cause: str = Field(default="")
    final_resolution: str = Field(default="")
    comment: str = Field(default="")
    environment: str = Field(default="")
    service_name: str = Field(default="")
    events: list[dict[str, Any]] = Field(default_factory=list)


class ManualExperienceCreateRequest(BaseModel):
    symptoms: str
    root_cause: str
    resolution: str
    evidence_summary: str = Field(default="")
    environment: str = Field(default="")
    service_name: str = Field(default="")
    confidence: float = 0.8


class ExperienceMemoryUpdateRequest(BaseModel):
    enabled: bool | None = None
    confidence: float | None = None


class ServiceUpsertRequest(BaseModel):
    environment: str = "prod"
    owner_team: str = ""
    owner_user: str = ""
    description: str = ""
    enabled: bool = True


class ServiceBaselineRequest(BaseModel):
    service_name: str
    environment: str = "prod"
    metric_name: Literal["cpu", "memory", "qps", "p95"] | str
    min_value: float
    max_value: float
    unit: str = ""
    sample_window: str = ""


class UserPreferenceRequest(BaseModel):
    default_environment: str = ""
    language: str = "zh-CN"
    detail_level: Literal["brief", "normal", "detailed"] = "normal"
    focused_services: list[str] = Field(default_factory=list)
    notes: str = ""
