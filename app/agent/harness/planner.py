"""Lightweight planning for the unified harness.

By default planning is a deterministic, no-extra-LLM rule pass. When
``harness_llm_planning_enabled`` is on and an LLM client is available, a single
planning call refines the todos / required evidence; any failure falls back to
the rule-based plan so the loop never breaks on planning.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.config import config
from app.core.llm_client import ChatMessage
from app.core.runtime_tools import RuntimeTool

_PLANNER_SYSTEM = (
    "你是只读 OnCall 排查的规划助手。基于用户问题、路由焦点和可用工具，"
    "输出一个简洁的排查计划。只返回 JSON，对象包含字段："
    '"todos"（3-6 条排查步骤字符串）与 "required_evidence"（2-4 条关键证据类型字符串）。'
    '可选字段 "required_params" 为数组，每项包含 name、prompt、aliases、default、reason；'
    "若当前问题不需要追问任何参数，可返回空数组。"
    "不要包含任何处置类动作（发布/回滚/扩缩容/删改数据），不要输出 JSON 以外的内容。"
)


@dataclass(frozen=True, slots=True)
class RequiredParam:
    name: str
    prompt: str
    aliases: list[str] = field(default_factory=list)
    default: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class HarnessPlan:
    todos: list[str]
    required_evidence: list[str]
    focus_route: str
    required_params: list[RequiredParam] = field(default_factory=list)
    available_tools: list[str] = field(default_factory=list)
    history_turns: int = 0


class LightweightPlanner:
    """Create a request-specific plan, optionally refined by one LLM call."""

    def create(
        self,
        *,
        message: str,
        route_decision: Any,
        tools: Sequence[RuntimeTool],
        history_turns: int,
    ) -> HarnessPlan:
        route = str(getattr(route_decision, "route", "") or "diagnosis")
        tool_names = [
            tool.name
            for tool in tools
            if tool.name not in {"context_read", "context_note", "read_attachment"}
        ]
        required_evidence = _required_evidence_for_route(route)
        required_params = _required_params_for_route(route)

        todos = ["理解当前请求、只读边界和可用历史上下文"]
        if history_turns:
            todos.append(f"回顾最近 {history_turns} 轮上下文，避免重复取证")
        if tool_names:
            todos.append(
                f"优先围绕 {route} 焦点选择最相关工具取证：{', '.join(tool_names[:4])}"
            )
        else:
            todos.append("当前无可用工具，回答中必须显式说明证据缺口")
        if required_evidence:
            todos.append(f"核对关键证据类型：{'、'.join(required_evidence)}")
        todos.append("定稿前自检：结论是否由工具证据或历史上下文支撑")

        return HarnessPlan(
            todos=todos,
            required_evidence=required_evidence,
            focus_route=route,
            required_params=required_params,
            available_tools=tool_names,
            history_turns=history_turns,
        )

    async def acreate(
        self,
        *,
        message: str,
        route_decision: Any,
        tools: Sequence[RuntimeTool],
        history_turns: int,
        llm_client: Any | None = None,
    ) -> HarnessPlan:
        base = self.create(
            message=message,
            route_decision=route_decision,
            tools=tools,
            history_turns=history_turns,
        )
        if not getattr(config, "harness_llm_planning_enabled", False) or llm_client is None:
            return base
        try:
            refined = await self._llm_plan(message=message, base=base, llm_client=llm_client)
        except Exception as exc:  # planning must never break the loop
            logger.warning(f"harness LLM 规划失败，回退规则版：{exc}")
            return base
        return refined or base

    def replan(
        self,
        plan: HarnessPlan,
        *,
        trigger: str,
        gaps: Sequence[str] | None = None,
        failed_tools: Sequence[str] | None = None,
        aux_routes: Sequence[str] | None = None,
    ) -> HarnessPlan:
        """Rule-based mid-loop replan (no extra LLM by default)."""
        return rule_replan(
            plan,
            trigger=trigger,
            gaps=gaps,
            failed_tools=failed_tools,
            aux_routes=aux_routes,
        )

    async def _llm_plan(
        self,
        *,
        message: str,
        base: HarnessPlan,
        llm_client: Any,
    ) -> HarnessPlan | None:
        user_prompt = (
            f"用户问题：{message}\n"
            f"路由焦点：{base.focus_route}\n"
            f"可用工具：{', '.join(base.available_tools) or '（无）'}\n"
            f"默认必需参数：{_format_required_params(base.required_params) or '（无）'}\n"
            f"历史轮数：{base.history_turns}\n"
            "请给出 JSON 计划。可选字段 required_params 为数组，每项包含 name、prompt、aliases、default、reason。"
        )
        response = await llm_client.complete(
            [
                ChatMessage(role="system", content=_PLANNER_SYSTEM),
                ChatMessage(role="user", content=user_prompt),
            ],
            temperature=0,
        )
        data = _extract_json(response.content)
        todos = [str(item).strip() for item in data.get("todos", []) if str(item).strip()]
        required = [
            str(item).strip() for item in data.get("required_evidence", []) if str(item).strip()
        ]
        required_params = (
            _parse_required_params(data.get("required_params"))
            if "required_params" in data
            else base.required_params
        )
        if not todos:
            return None
        return HarnessPlan(
            todos=todos,
            required_evidence=required or base.required_evidence,
            focus_route=base.focus_route,
            required_params=required_params,
            available_tools=base.available_tools,
            history_turns=base.history_turns,
        )


def _required_evidence_for_route(route: str) -> list[str]:
    if route == "metric":
        return ["指标曲线或告警", "异常时间窗口"]
    if route == "log":
        return ["错误日志样本", "日志聚类或关键堆栈"]
    if route == "change":
        return ["近期变更记录", "变更时间与异常时间对齐"]
    if route == "knowledge":
        return ["知识库检索结果", "适用前提"]
    return ["指标/日志/变更至少一种证据", "证据缺口说明"]


def rule_replan(
    plan: HarnessPlan,
    *,
    trigger: str,
    gaps: Sequence[str] | None = None,
    failed_tools: Sequence[str] | None = None,
    aux_routes: Sequence[str] | None = None,
) -> HarnessPlan:
    """Deterministically revise todos / required_evidence after investigation stalls.

    LLM refinement is intentionally out of scope here; callers may optionally pass
    the result through ``LightweightPlanner._llm_plan`` when LLM planning is on.
    """
    gaps = [str(g).strip() for g in (gaps or []) if str(g).strip()]
    failed_tools = [str(t).strip() for t in (failed_tools or []) if str(t).strip()]
    aux_routes = [str(r).strip() for r in (aux_routes or []) if str(r).strip()]

    todos = list(plan.todos)
    required = list(plan.required_evidence)
    focus = plan.focus_route

    # Promote still-missing evidence classes to the front of the todo list.
    if gaps:
        gap_todo = f"优先补齐证据缺口：{'、'.join(gaps[:4])}"
        todos = [gap_todo, *[t for t in todos if t != gap_todo]]
        # Keep original required classes; append explicit gap notice if absent.
        if "证据缺口说明" not in required:
            required = [*required, "证据缺口说明"]

    if failed_tools:
        fail_todo = (
            f"先前工具失败（{', '.join(failed_tools[:4])}），"
            "换时间窗/实例或改用同类只读工具再取证"
        )
        todos = [fail_todo, *[t for t in todos if t != fail_todo]]
        if focus == "metric" and "指标曲线或告警" not in required:
            required = ["指标曲线或告警", *required]
        if focus == "log" and "错误日志样本" not in required:
            required = ["错误日志样本", *required]

    if aux_routes:
        for aux in aux_routes:
            for item in _required_evidence_for_route(aux):
                if item not in required:
                    required.append(item)
            aux_todo = (
                f"跨域补充：优先 delegate_parallel 或 delegate_to_expert({aux}) / {aux} 类只读工具"
            )
            if aux_todo not in todos:
                todos.append(aux_todo)

    if focus == "change":
        # Never invent change facts; force explicit missing-source language.
        change_todo = "变更源若不可用，必须声明 missing_change_datasource，禁止编造版本/操作人"
        if change_todo not in todos:
            todos.append(change_todo)

    # Deduplicate while preserving order.
    todos = list(dict.fromkeys(t for t in todos if t))
    required = list(dict.fromkeys(r for r in required if r))

    # Cap size so replan does not bloat the system message indefinitely.
    todos = todos[:8]
    required = required[:6]

    return HarnessPlan(
        todos=todos,
        required_evidence=required,
        focus_route=plan.focus_route,
        required_params=list(plan.required_params),
        available_tools=list(plan.available_tools),
        history_turns=plan.history_turns,
    )


def _required_params_for_route(route: str) -> list[RequiredParam]:
    if route == "metric":
        return [
            RequiredParam(
                name="target",
                prompt="主机/IP/instance/服务/pod/job 任一标识",
                aliases=["主机", "host", "ip", "instance", "实例", "服务", "service", "pod", "job"],
                reason="指标查询需要可唯一定位的目标对象；缺少该参数时不能可靠调用指标工具。",
            )
        ]
    if route == "log":
        return [
            RequiredParam(
                name="log_scope",
                prompt="服务/应用/pod/namespace 任一日志范围，或明确的日志关键词",
                aliases=["服务", "service", "应用", "app", "pod", "namespace", "关键词", "keyword"],
                reason="日志查询需要范围或关键词，否则无法收窄到有意义的日志样本。",
            )
        ]
    if route == "change":
        return [
            RequiredParam(
                name="change_scope",
                prompt="服务/应用/模块名称，或明确的变更对象",
                aliases=["服务", "service", "应用", "app", "模块", "module", "变更对象"],
                reason="变更查询需要对象范围，否则无法判断哪些发布或配置变更相关。",
            )
        ]
    return []


def _format_required_params(params: list[RequiredParam]) -> str:
    return "；".join(f"{item.name}: {item.prompt}" for item in params)


def _parse_required_params(raw: object) -> list[RequiredParam]:
    if not isinstance(raw, list):
        return []
    params: list[RequiredParam] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        prompt = str(item.get("prompt") or "").strip()
        if not name or not prompt:
            continue
        aliases_raw = item.get("aliases") or []
        aliases = (
            [str(alias).strip() for alias in aliases_raw if str(alias).strip()]
            if isinstance(aliases_raw, list)
            else []
        )
        params.append(
            RequiredParam(
                name=name,
                prompt=prompt,
                aliases=aliases,
                default=str(item.get("default") or "").strip(),
                reason=str(item.get("reason") or "").strip(),
            )
        )
    return params


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort parse of a JSON object from a possibly fenced LLM response."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if "\n" in raw:
            first, rest = raw.split("\n", 1)
            if first.strip().lower() in {"json", ""}:
                raw = rest
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(raw[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
