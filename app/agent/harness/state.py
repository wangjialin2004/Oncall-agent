"""Runtime state for the unified harness loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agent.agent_loop import estimate_tokens


@dataclass(slots=True)
class HarnessLimits:
    max_steps: int
    token_budget: int
    timeout_seconds: float
    # 连续“重复工具调用且无新增证据”达到该阈值则提前收尾（防空转）
    no_progress_limit: int = 2


@dataclass(slots=True)
class HarnessState:
    trace_id: str
    session_id: str
    owner_key: str = ""
    route: str = "harness"
    route_reason: str = ""
    case_id: str = ""
    step: int = 0
    answer_parts: list[str] = field(default_factory=list)
    timeline_events: list[dict[str, Any]] = field(default_factory=list)
    usage_total: dict[str, int] = field(default_factory=dict)
    token_estimate: int = 0

    def add_usage(self, usage: dict[str, Any]) -> None:
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                self.usage_total[key] = self.usage_total.get(key, 0) + int(value)

    def add_text_budget(self, text: str) -> None:
        self.token_estimate += estimate_tokens(text or "")

    def append_answer(self, text: str) -> None:
        if text:
            self.answer_parts.append(text)
            self.add_text_budget(text)

    @property
    def answer(self) -> str:
        return "".join(self.answer_parts)

    def over_budget(self, limits: HarnessLimits) -> bool:
        llm_total = self.usage_total.get("total_tokens", 0)
        return max(llm_total, self.token_estimate) >= limits.token_budget
