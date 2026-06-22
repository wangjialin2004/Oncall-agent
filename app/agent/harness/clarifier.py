"""Planner-driven missing-parameter clarification for the harness."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.agent.harness.planner import HarnessPlan, RequiredParam
from app.core.llm_client import ChatMessage
from app.core.runtime_tools import RuntimeTool

_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_LATIN_IDENTIFIER_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_.:/-]{2,}\b")
_KEY_VALUE_SEPARATORS = ("=", ":", "：", "是", "为")
_FILLER_IDENTIFIERS = {
    "app",
    "cpu",
    "disk",
    "host",
    "instance",
    "ip",
    "job",
    "memory",
    "pod",
    "service",
}


@dataclass(frozen=True, slots=True)
class ClarificationRequest:
    """A user-facing clarification that should stop the current harness run."""

    missing_params: list[str]
    question: str
    reason: str
    evidence_gap: str
    defaults: dict[str, str] = field(default_factory=dict)


class MissingParameterClarifier:
    """Ask for planner-declared parameters that cannot be inferred safely.

    The planner owns *which* parameters matter for the current route/task. This
    class only performs a generic availability check over current input, recent
    history, parameter defaults, and whether a tool appears capable of listing or
    discovering that parameter automatically.
    """

    def check(
        self,
        *,
        message: str,
        plan: HarnessPlan,
        tools: Sequence[RuntimeTool],
        history_messages: Sequence[ChatMessage],
    ) -> ClarificationRequest | None:
        params = list(plan.required_params or [])
        if not params:
            return None

        current = message or ""
        history_text = "\n".join(
            item.content for item in history_messages[-4:] if item.content
        )
        combined = f"{history_text}\n{current}".strip()
        missing: list[RequiredParam] = []

        for param in params:
            if param.default:
                continue
            if self._is_param_present(param, combined):
                continue
            if self._can_tool_discover_param(param, tools):
                continue
            missing.append(param)

        if not missing:
            return None
        return self._build_request(missing)

    def _is_param_present(self, param: RequiredParam, text: str) -> bool:
        if not text.strip():
            return False
        aliases = [alias for alias in param.aliases if alias]
        if _IP_RE.search(text):
            return self._looks_like_target_param(param)

        for alias in aliases:
            if self._has_keyed_value(alias, text):
                return True

        if self._looks_like_target_param(param):
            return any(
                token.lower() not in _FILLER_IDENTIFIERS
                for token in _LATIN_IDENTIFIER_RE.findall(text)
            )
        return False

    @staticmethod
    def _has_keyed_value(alias: str, text: str) -> bool:
        escaped = re.escape(alias)
        separators = "|".join(re.escape(item) for item in _KEY_VALUE_SEPARATORS)
        pattern = re.compile(
            rf"{escaped}\s*(?:{separators})\s*([A-Za-z0-9_.:/-]{{2,}})",
            re.IGNORECASE,
        )
        return bool(pattern.search(text))

    @staticmethod
    def _can_tool_discover_param(param: RequiredParam, tools: Sequence[RuntimeTool]) -> bool:
        aliases = {param.name.lower(), *(alias.lower() for alias in param.aliases)}
        for tool in tools:
            haystack = f"{tool.name} {tool.description}".lower()
            can_discover = any(word in haystack for word in ("list", "discover", "枚举", "列出"))
            mentions_param = any(alias and alias in haystack for alias in aliases)
            if can_discover and mentions_param:
                return True
        return False

    @staticmethod
    def _looks_like_target_param(param: RequiredParam) -> bool:
        values = {param.name.lower(), *(alias.lower() for alias in param.aliases)}
        return bool(
            values
            & {
                "target",
                "scope",
                "host",
                "ip",
                "instance",
                "service",
                "app",
                "pod",
                "job",
                "log_scope",
                "change_scope",
            }
        )

    @staticmethod
    def _build_request(missing: Sequence[RequiredParam]) -> ClarificationRequest:
        prompts = [param.prompt for param in missing]
        reasons = [param.reason for param in missing if param.reason]
        defaults = {param.prompt: param.default for param in missing if param.default}
        question = "我还缺少继续排查所需的关键信息。\n\n请补充：" + "；".join(prompts) + "。"
        return ClarificationRequest(
            missing_params=prompts,
            question=question,
            reason="；".join(reasons) or "缺少 planner 判定为必需、且当前工具无法自动获取的参数。",
            evidence_gap="缺少必要参数：" + "；".join(prompts),
            defaults=defaults,
        )
