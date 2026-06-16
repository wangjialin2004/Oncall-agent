# OnCall Multi-Agent Diagnosis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Supervisor-orchestrated OnCall multi-agent diagnosis workflow with visible timeline events.

**Architecture:** Keep the existing Router/RAG/AIOps API surfaces, but evolve the AIOps LangGraph into a coordinator graph with explicit Triage, Planner, Evidence Collector, Diagnosis, and Report responsibilities. Add a shared event schema so the API can stream and return the same normalized timeline.

**Tech Stack:** FastAPI, LangGraph, LangChain, Pydantic, pytest, existing MCP tools, existing SQLite diagnosis memory.

---

## Scope

This plan implements the approved spec in `docs/superpowers/specs/2026-06-15-oncall-multi-agent-design.md`.

It does not add mutating remediation, new external monitoring providers, or frontend layout changes. It keeps existing endpoints compatible and focuses on backend workflow, events, and tests.

## File Structure

- Create `app/agent/aiops/events.py`: normalized event constructors and append helper.
- Modify `app/agent/aiops/state.py`: add `OnCallState` while keeping `PlanExecuteState`.
- Create `app/agent/aiops/triage.py`: Triage Agent node and deterministic fallback incident builder.
- Create `app/agent/aiops/reporter.py`: Report Agent node and deterministic fallback report builder.
- Create `app/agent/aiops/plan_utils.py`: helpers to normalize plan steps and read the next executable step.
- Modify `app/agent/aiops/planner.py`: return structured plan steps and planner events.
- Modify `app/agent/aiops/executor.py`: accept structured plan steps, emit evidence/tool events, and preserve old behavior.
- Create `app/agent/aiops/diagnosis.py`: Diagnosis Agent node and loop routing helper.
- Modify `app/agent/aiops/__init__.py`: export new agent nodes and state.
- Modify `app/services/aiops_service.py`: build the coordinator graph and include normalized events in completion.
- Modify `app/services/router_service.py`: collect AIOps events for `/api/assistant` responses.
- Modify `app/api/aiops.py`: document and stream normalized events without changing the SSE shape.
- Add focused tests under `tests/`.

---

### Task 1: Shared Event Schema And OnCall State

**Files:**
- Create: `app/agent/aiops/events.py`
- Modify: `app/agent/aiops/state.py`
- Modify: `app/agent/aiops/__init__.py`
- Test: `tests/test_aiops_events_state.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_aiops_events_state.py`:

```python
from app.agent.aiops.events import (
    append_event,
    make_agent_event,
    make_decision_event,
    make_tool_event,
)


def test_make_agent_event_uses_normalized_shape():
    event = make_agent_event(
        agent="triage",
        stage="triage",
        status="completed",
        summary="Incident structured",
        payload={"incident_type": "slow_response"},
    )

    assert event == {
        "type": "agent_event",
        "agent": "triage",
        "stage": "triage",
        "status": "completed",
        "summary": "Incident structured",
        "payload": {"incident_type": "slow_response"},
    }


def test_make_tool_event_includes_evidence_id():
    event = make_tool_event(
        agent="evidence_collector",
        tool="query_metrics_alerts",
        status="completed",
        evidence_id="ev-1",
        summary="Latency rose after 14:20",
        payload={"duration_ms": 12},
    )

    assert event["type"] == "tool_event"
    assert event["agent"] == "evidence_collector"
    assert event["tool"] == "query_metrics_alerts"
    assert event["evidence_id"] == "ev-1"
    assert event["payload"] == {"duration_ms": 12}


def test_make_decision_event_defaults_payload_to_empty_dict():
    event = make_decision_event(
        agent="diagnosis",
        status="evidence_insufficient",
        summary="Need database evidence",
    )

    assert event == {
        "type": "decision_event",
        "agent": "diagnosis",
        "status": "evidence_insufficient",
        "summary": "Need database evidence",
        "payload": {},
    }


def test_append_event_preserves_existing_events():
    state = {"events": [{"type": "agent_event", "agent": "triage"}]}
    event = make_agent_event(
        agent="planner",
        stage="planning",
        status="completed",
        summary="Generated plan",
    )

    update = append_event(state, event)

    assert update == {
        "events": [
            {"type": "agent_event", "agent": "triage"},
            event,
        ]
    }
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
python -m pytest tests/test_aiops_events_state.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.agent.aiops.events'`.

- [ ] **Step 3: Add event helpers**

Create `app/agent/aiops/events.py`:

```python
"""Normalized timeline events for the OnCall multi-agent workflow."""

from __future__ import annotations

from typing import Any


OnCallEvent = dict[str, Any]


def make_agent_event(
    *,
    agent: str,
    stage: str,
    status: str,
    summary: str,
    payload: dict[str, Any] | None = None,
) -> OnCallEvent:
    return {
        "type": "agent_event",
        "agent": agent,
        "stage": stage,
        "status": status,
        "summary": summary,
        "payload": payload or {},
    }


def make_tool_event(
    *,
    agent: str,
    tool: str,
    status: str,
    evidence_id: str,
    summary: str,
    payload: dict[str, Any] | None = None,
) -> OnCallEvent:
    return {
        "type": "tool_event",
        "agent": agent,
        "tool": tool,
        "status": status,
        "evidence_id": evidence_id,
        "summary": summary,
        "payload": payload or {},
    }


def make_decision_event(
    *,
    agent: str,
    status: str,
    summary: str,
    payload: dict[str, Any] | None = None,
) -> OnCallEvent:
    return {
        "type": "decision_event",
        "agent": agent,
        "status": status,
        "summary": summary,
        "payload": payload or {},
    }


def append_event(state: dict[str, Any], event: OnCallEvent) -> dict[str, list[OnCallEvent]]:
    return {"events": list(state.get("events", [])) + [event]}
```

- [ ] **Step 4: Add `OnCallState` without breaking existing imports**

Modify `app/agent/aiops/state.py` so it contains both state types:

```python
"""
State definitions for the AIOps and OnCall diagnosis workflows.
"""

import operator
from typing import Annotated, Any, NotRequired, TypedDict


class PlanExecuteState(TypedDict):
    """Existing Plan-Execute-Replan state."""

    input: str
    plan: list[Any]
    past_steps: Annotated[list[tuple], operator.add]
    response: str
    session_id: NotRequired[str]
    case_id: NotRequired[str]


class OnCallState(TypedDict):
    """Supervisor-orchestrated OnCall multi-agent state."""

    input: str
    session_id: str
    case_id: str
    route: NotRequired[str]
    route_reason: NotRequired[str]
    incident: NotRequired[dict[str, Any]]
    plan: list[dict[str, Any]]
    past_steps: Annotated[list[dict[str, Any]], operator.add]
    evidence: Annotated[list[dict[str, Any]], operator.add]
    diagnosis: NotRequired[dict[str, Any]]
    response: str
    iteration: int
    max_iterations: int
    events: list[dict[str, Any]]
```

Modify `app/agent/aiops/__init__.py` exports:

```python
from .executor import executor
from .planner import planner
from .replanner import replanner
from .state import OnCallState, PlanExecuteState

__all__ = [
    "OnCallState",
    "PlanExecuteState",
    "planner",
    "executor",
    "replanner",
]
```

- [ ] **Step 5: Run tests**

Run:

```bash
python -m pytest tests/test_aiops_events_state.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/agent/aiops/events.py app/agent/aiops/state.py app/agent/aiops/__init__.py tests/test_aiops_events_state.py
git commit -m "feat: add oncall event state primitives"
```

---

### Task 2: Triage Agent

**Files:**
- Create: `app/agent/aiops/triage.py`
- Modify: `app/agent/aiops/__init__.py`
- Test: `tests/test_aiops_triage.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_aiops_triage.py`:

```python
import pytest

from app.agent.aiops import triage as triage_node
from app.agent.aiops.triage import build_minimal_incident


def test_build_minimal_incident_extracts_basic_slow_response_signal():
    incident = build_minimal_incident("checkout-api 最近一直转圈，接口响应很慢")

    assert incident["incident_type"] == "slow_response"
    assert incident["service_name"] == "checkout-api"
    assert incident["time_window"] == "recent"
    assert incident["severity"] == "P2"
    assert "checkout-api 最近一直转圈，接口响应很慢" in incident["symptoms"]
    assert incident["evidence_needs"] == ["metrics", "logs", "knowledge"]
    assert incident["confidence"] == 0.4


@pytest.mark.asyncio
async def test_triage_node_returns_generated_incident_and_event(monkeypatch):
    async def fake_generate_incident(input_text):
        assert input_text == "payment service 500 errors"
        return {
            "incident_type": "error_rate",
            "service_name": "payment",
            "time_window": "last_30_minutes",
            "severity": "P1",
            "symptoms": ["500 errors"],
            "missing_fields": [],
            "evidence_needs": ["metrics", "logs"],
            "confidence": 0.82,
        }

    monkeypatch.setattr("app.agent.aiops.triage.generate_incident", fake_generate_incident)

    update = await triage_node({"input": "payment service 500 errors", "events": []})

    assert update["incident"]["incident_type"] == "error_rate"
    assert update["events"][-1]["agent"] == "triage"
    assert update["events"][-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_triage_node_falls_back_to_minimal_incident(monkeypatch):
    async def fake_generate_incident(input_text):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr("app.agent.aiops.triage.generate_incident", fake_generate_incident)

    update = await triage_node({"input": "disk full on api", "events": []})

    assert update["incident"]["incident_type"] == "disk"
    assert update["incident"]["confidence"] == 0.4
    assert update["events"][-1]["status"] == "degraded"
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
python -m pytest tests/test_aiops_triage.py -q
```

Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `triage`.

- [ ] **Step 3: Add Triage Agent**

Create `app/agent/aiops/triage.py`:

```python
"""Triage Agent for structuring raw OnCall incident descriptions."""

from __future__ import annotations

import re
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_qwq import ChatQwen
from loguru import logger
from pydantic import BaseModel, Field

from app.config import config

from .events import make_agent_event


class Incident(BaseModel):
    incident_type: Literal[
        "cpu",
        "memory",
        "disk",
        "slow_response",
        "service_unavailable",
        "error_rate",
        "unknown",
    ] = Field(description="Primary incident category.")
    service_name: str = Field(default="", description="Affected service name if known.")
    time_window: str = Field(default="recent", description="Time window to inspect.")
    severity: str = Field(default="P2", description="P0/P1/P2/P3 severity.")
    symptoms: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    evidence_needs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0)


def _detect_incident_type(text: str) -> str:
    lowered = text.lower()
    if "cpu" in lowered:
        return "cpu"
    if any(token in lowered for token in ("memory", "内存", "oom")):
        return "memory"
    if any(token in lowered for token in ("disk", "磁盘")):
        return "disk"
    if any(token in lowered for token in ("500", "error", "错误率", "报错")):
        return "error_rate"
    if any(token in lowered for token in ("unavailable", "不可用", "挂了", "down")):
        return "service_unavailable"
    if any(token in lowered for token in ("slow", "latency", "响应慢", "转圈", "超时")):
        return "slow_response"
    return "unknown"


def _extract_service_name(text: str) -> str:
    match = re.search(r"([A-Za-z][A-Za-z0-9_-]*(?:-api|-service|_api|_service)?)", text)
    return match.group(1) if match else ""


def build_minimal_incident(input_text: str) -> dict[str, Any]:
    service_name = _extract_service_name(input_text)
    return {
        "incident_type": _detect_incident_type(input_text),
        "service_name": service_name,
        "time_window": "recent",
        "severity": "P2",
        "symptoms": [input_text.strip()] if input_text.strip() else [],
        "missing_fields": ["service_name"] if not service_name else [],
        "evidence_needs": ["metrics", "logs", "knowledge"],
        "confidence": 0.4,
    }


async def generate_incident(input_text: str) -> dict[str, Any]:
    model = ChatQwen(
        model=config.rag_model,
        api_key=config.dashscope_api_key,
        temperature=0,
        streaming=False,
    )
    classifier = model.with_structured_output(Incident)
    result = await classifier.ainvoke(
        [
            SystemMessage(
                content=(
                    "You are an OnCall triage agent. Convert the user incident into "
                    "structured JSON. Use unknown fields only when the user did not provide them. "
                    "Set evidence_needs from metrics, logs, knowledge."
                )
            ),
            HumanMessage(content=input_text),
        ]
    )
    if isinstance(result, Incident):
        return result.model_dump()
    return Incident.model_validate(result).model_dump()


async def triage(state: dict[str, Any]) -> dict[str, Any]:
    input_text = str(state.get("input", ""))
    try:
        incident = await generate_incident(input_text)
        status = "completed"
        summary = f"Structured incident as {incident.get('incident_type', 'unknown')}"
    except Exception as exc:
        logger.warning(f"Triage Agent degraded to minimal incident: {exc}")
        incident = build_minimal_incident(input_text)
        status = "degraded"
        summary = f"Used minimal incident fallback as {incident['incident_type']}"

    event = make_agent_event(
        agent="triage",
        stage="triage",
        status=status,
        summary=summary,
        payload={"incident": incident},
    )
    return {"incident": incident, "events": list(state.get("events", [])) + [event]}
```

- [ ] **Step 4: Export the Triage Agent**

Modify `app/agent/aiops/__init__.py`:

```python
from .executor import executor
from .planner import planner
from .replanner import replanner
from .state import OnCallState, PlanExecuteState
from .triage import triage

__all__ = [
    "OnCallState",
    "PlanExecuteState",
    "planner",
    "executor",
    "replanner",
    "triage",
]
```

- [ ] **Step 5: Run tests**

Run:

```bash
python -m pytest tests/test_aiops_triage.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/agent/aiops/triage.py app/agent/aiops/__init__.py tests/test_aiops_triage.py
git commit -m "feat: add oncall triage agent"
```

---

### Task 3: Report Agent

**Files:**
- Create: `app/agent/aiops/reporter.py`
- Modify: `app/agent/aiops/__init__.py`
- Test: `tests/test_aiops_reporter.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_aiops_reporter.py`:

```python
import pytest

from app.agent.aiops import reporter as reporter_node
from app.agent.aiops.reporter import build_fallback_report


def test_build_fallback_report_includes_required_sections():
    report = build_fallback_report(
        {
            "incident": {"incident_type": "slow_response", "service_name": "checkout-api"},
            "evidence": [
                {
                    "evidence_id": "ev-1",
                    "summary": "P95 latency rose above 3s",
                    "status": "completed",
                }
            ],
            "diagnosis": {
                "status": "root_cause_ready",
                "root_cause_candidates": [{"cause": "DB saturation", "confidence": 0.75}],
            },
        }
    )

    assert "# OnCall Diagnosis Report" in report
    assert "checkout-api" in report
    assert "P95 latency rose above 3s" in report
    assert "DB saturation" in report
    assert "Recommended Actions" in report


@pytest.mark.asyncio
async def test_reporter_node_uses_generated_report(monkeypatch):
    async def fake_generate_report(state):
        return "# Report\nGenerated"

    monkeypatch.setattr("app.agent.aiops.reporter.generate_report", fake_generate_report)

    update = await reporter_node(
        {
            "incident": {"incident_type": "error_rate"},
            "evidence": [],
            "diagnosis": {},
            "events": [],
        }
    )

    assert update["response"] == "# Report\nGenerated"
    assert update["events"][-1]["agent"] == "report"
    assert update["events"][-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_reporter_node_falls_back_when_generation_fails(monkeypatch):
    async def fake_generate_report(state):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr("app.agent.aiops.reporter.generate_report", fake_generate_report)

    update = await reporter_node(
        {
            "incident": {"incident_type": "disk", "service_name": "api"},
            "evidence": [],
            "diagnosis": {},
            "events": [],
        }
    )

    assert "# OnCall Diagnosis Report" in update["response"]
    assert update["events"][-1]["status"] == "degraded"
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
python -m pytest tests/test_aiops_reporter.py -q
```

Expected: FAIL with `ImportError` for `reporter`.

- [ ] **Step 3: Add Report Agent**

Create `app/agent/aiops/reporter.py`:

```python
"""Report Agent for final OnCall diagnosis reports."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_qwq import ChatQwen
from loguru import logger

from app.config import config

from .events import make_agent_event


def _format_evidence(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return "- No tool evidence was collected."
    lines = []
    for item in evidence:
        evidence_id = item.get("evidence_id", "")
        status = item.get("status", "")
        summary = item.get("summary", "")
        lines.append(f"- `{evidence_id}` [{status}] {summary}".strip())
    return "\n".join(lines)


def _format_candidates(diagnosis: dict[str, Any]) -> str:
    candidates = diagnosis.get("root_cause_candidates") or []
    if not candidates:
        return "- Root cause is not confirmed with current evidence."
    lines = []
    for item in candidates:
        cause = item.get("cause", "")
        confidence = item.get("confidence", 0)
        lines.append(f"- {cause} (confidence: {confidence})")
    return "\n".join(lines)


def build_fallback_report(state: dict[str, Any]) -> str:
    incident = state.get("incident", {})
    diagnosis = state.get("diagnosis", {})
    evidence = state.get("evidence", [])
    service_name = incident.get("service_name") or "unknown service"
    incident_type = incident.get("incident_type") or "unknown"
    confidence = diagnosis.get("confidence", "unknown")

    return "\n".join(
        [
            "# OnCall Diagnosis Report",
            "",
            "## Incident Summary",
            f"- Service: {service_name}",
            f"- Type: {incident_type}",
            f"- Diagnosis status: {diagnosis.get('status', 'unknown')}",
            f"- Confidence: {confidence}",
            "",
            "## Evidence",
            _format_evidence(evidence),
            "",
            "## Root Cause Candidates",
            _format_candidates(diagnosis),
            "",
            "## Missing Evidence",
            "\n".join(f"- {item}" for item in diagnosis.get("missing_evidence", [])) or "- None recorded.",
            "",
            "## Recommended Actions",
            "- Review the evidence above before applying any remediation.",
            "- Collect missing evidence when confidence is low.",
        ]
    )


async def generate_report(state: dict[str, Any]) -> str:
    model = ChatQwen(
        model=config.rag_model,
        api_key=config.dashscope_api_key,
        temperature=0,
        streaming=False,
    )
    prompt = [
        SystemMessage(
            content=(
                "You are an OnCall report agent. Produce a concise Markdown report. "
                "Separate confirmed facts, inferred causes, missing evidence, and recommended actions. "
                "Do not invent evidence."
            )
        ),
        HumanMessage(content=f"Incident: {state.get('incident', {})}"),
        HumanMessage(content=f"Evidence: {state.get('evidence', [])}"),
        HumanMessage(content=f"Diagnosis: {state.get('diagnosis', {})}"),
    ]
    result = await model.ainvoke(prompt)
    return result.content if hasattr(result, "content") else str(result)


async def reporter(state: dict[str, Any]) -> dict[str, Any]:
    try:
        report = await generate_report(state)
        status = "completed"
        summary = "Generated final OnCall diagnosis report"
    except Exception as exc:
        logger.warning(f"Report Agent degraded to fallback report: {exc}")
        report = build_fallback_report(state)
        status = "degraded"
        summary = "Generated fallback OnCall diagnosis report"

    event = make_agent_event(
        agent="report",
        stage="reporting",
        status=status,
        summary=summary,
        payload={"report_length": len(report)},
    )
    return {"response": report, "events": list(state.get("events", [])) + [event]}
```

- [ ] **Step 4: Export the Report Agent**

Modify `app/agent/aiops/__init__.py`:

```python
from .executor import executor
from .planner import planner
from .replanner import replanner
from .reporter import reporter
from .state import OnCallState, PlanExecuteState
from .triage import triage

__all__ = [
    "OnCallState",
    "PlanExecuteState",
    "planner",
    "executor",
    "replanner",
    "reporter",
    "triage",
]
```

- [ ] **Step 5: Run tests**

Run:

```bash
python -m pytest tests/test_aiops_reporter.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/agent/aiops/reporter.py app/agent/aiops/__init__.py tests/test_aiops_reporter.py
git commit -m "feat: add oncall report agent"
```

---

### Task 4: Structured Plan Helpers And Planner Output

**Files:**
- Create: `app/agent/aiops/plan_utils.py`
- Modify: `app/agent/aiops/planner.py`
- Test: `tests/test_aiops_plan_utils.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_aiops_plan_utils.py`:

```python
from app.agent.aiops.plan_utils import normalize_plan_steps, plan_step_text, pop_next_plan_step


def test_normalize_plan_steps_converts_strings_to_structured_steps():
    steps = normalize_plan_steps(["Check metrics", "Search logs"])

    assert steps == [
        {
            "step_id": "plan-1",
            "description": "Check metrics",
            "tool_category": "unknown",
            "expected_evidence": "Evidence requested by the plan step.",
        },
        {
            "step_id": "plan-2",
            "description": "Search logs",
            "tool_category": "unknown",
            "expected_evidence": "Evidence requested by the plan step.",
        },
    ]


def test_normalize_plan_steps_preserves_structured_fields():
    steps = normalize_plan_steps(
        [
            {
                "description": "Check latency",
                "tool_category": "monitor",
                "expected_evidence": "Latency trend",
            }
        ]
    )

    assert steps[0]["step_id"] == "plan-1"
    assert steps[0]["description"] == "Check latency"
    assert steps[0]["tool_category"] == "monitor"
    assert steps[0]["expected_evidence"] == "Latency trend"


def test_plan_step_text_accepts_string_and_dict():
    assert plan_step_text("Check metrics") == "Check metrics"
    assert plan_step_text({"description": "Check logs"}) == "Check logs"


def test_pop_next_plan_step_returns_next_step_and_remaining_steps():
    current, remaining = pop_next_plan_step(
        [
            {"step_id": "plan-1", "description": "Check metrics"},
            {"step_id": "plan-2", "description": "Search logs"},
        ]
    )

    assert current == {"step_id": "plan-1", "description": "Check metrics"}
    assert remaining == [{"step_id": "plan-2", "description": "Search logs"}]
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
python -m pytest tests/test_aiops_plan_utils.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `plan_utils`.

- [ ] **Step 3: Add plan utilities**

Create `app/agent/aiops/plan_utils.py`:

```python
"""Helpers for structured OnCall diagnosis plans."""

from __future__ import annotations

from typing import Any


def normalize_plan_steps(steps: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        if isinstance(step, dict):
            description = str(step.get("description") or step.get("step") or step)
            tool_category = str(step.get("tool_category") or "unknown")
            expected_evidence = str(
                step.get("expected_evidence") or "Evidence requested by the plan step."
            )
        else:
            description = str(step)
            tool_category = "unknown"
            expected_evidence = "Evidence requested by the plan step."
        normalized.append(
            {
                "step_id": str(step.get("step_id")) if isinstance(step, dict) and step.get("step_id") else f"plan-{index}",
                "description": description,
                "tool_category": tool_category,
                "expected_evidence": expected_evidence,
            }
        )
    return normalized


def plan_step_text(step: Any) -> str:
    if isinstance(step, dict):
        return str(step.get("description") or step)
    return str(step)


def pop_next_plan_step(plan: list[Any]) -> tuple[Any | None, list[Any]]:
    if not plan:
        return None, []
    return plan[0], plan[1:]
```

- [ ] **Step 4: Modify Planner to return structured steps and an event**

In `app/agent/aiops/planner.py`, import helpers:

```python
from .events import make_agent_event
from .plan_utils import normalize_plan_steps
```

After extracting `plan_steps`, replace the return with:

```python
structured_steps = normalize_plan_steps(plan_steps)
event = make_agent_event(
    agent="planner",
    stage="planning",
    status="completed",
    summary=f"Generated {len(structured_steps)} investigation steps.",
    payload={"plan": structured_steps},
)
return {"plan": structured_steps, "events": list(state.get("events", [])) + [event]}
```

Replace the existing fallback return with:

```python
fallback_steps = normalize_plan_steps(
    [
        {
            "description": "Collect current metrics for the affected service or system.",
            "tool_category": "monitor",
            "expected_evidence": "CPU, memory, latency, error-rate, or disk anomaly summary.",
        },
        {
            "description": "Search recent application logs for errors related to the incident.",
            "tool_category": "logs",
            "expected_evidence": "Error messages, exception stack traces, or timeout records.",
        },
        {
            "description": "Retrieve relevant runbook knowledge for the detected incident type.",
            "tool_category": "knowledge",
            "expected_evidence": "Known causes and recommended handling steps.",
        },
    ]
)
event = make_agent_event(
    agent="planner",
    stage="planning",
    status="degraded",
    summary="Generated fallback investigation plan.",
    payload={"plan": fallback_steps, "error": str(e)},
)
return {"plan": fallback_steps, "events": list(state.get("events", [])) + [event]}
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
python -m pytest tests/test_aiops_plan_utils.py tests/test_aiops_experience_planner.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/agent/aiops/plan_utils.py app/agent/aiops/planner.py tests/test_aiops_plan_utils.py
git commit -m "feat: structure oncall diagnosis plans"
```

---

### Task 5: Evidence Collector Event Output

**Files:**
- Modify: `app/agent/aiops/executor.py`
- Test: `tests/test_aiops_evidence_collector.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_aiops_evidence_collector.py`:

```python
from app.agent.aiops.executor import build_failed_step_update, build_success_step_update


def test_build_success_step_update_adds_past_step_evidence_and_event():
    step = {
        "step_id": "plan-1",
        "description": "Check metrics",
        "tool_category": "monitor",
        "expected_evidence": "Latency trend",
    }

    update = build_success_step_update(
        state={"events": [], "evidence": []},
        step=step,
        remaining_plan=[],
        result="Latency is high",
        evidence_records=[
            {
                "tool_name": "query_metrics_alerts",
                "tool_call_id": "call-1",
                "evidence_id": "ev-1",
                "source": "monitor",
                "success": True,
                "duration_ms": 9,
                "summary": "P95 latency high",
            }
        ],
    )

    assert update["plan"] == []
    assert update["past_steps"][0]["step_id"] == "plan-1"
    assert update["evidence"][0]["evidence_id"] == "ev-1"
    assert update["events"][-1]["type"] == "tool_event"
    assert update["events"][-1]["evidence_id"] == "ev-1"


def test_build_failed_step_update_records_failure_event():
    step = {"step_id": "plan-1", "description": "Check logs"}

    update = build_failed_step_update(
        state={"events": [], "evidence": []},
        step=step,
        remaining_plan=[],
        error=RuntimeError("tool unavailable"),
    )

    assert update["past_steps"][0]["status"] == "failed"
    assert update["evidence"][0]["status"] == "failed"
    assert "tool unavailable" in update["evidence"][0]["summary"]
    assert update["events"][-1]["status"] == "failed"
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
python -m pytest tests/test_aiops_evidence_collector.py -q
```

Expected: FAIL because `build_success_step_update` and `build_failed_step_update` do not exist.

- [ ] **Step 3: Add evidence update helpers**

Modify `app/agent/aiops/executor.py` imports:

```python
from .events import make_tool_event
from .plan_utils import plan_step_text, pop_next_plan_step
```

Add these helpers near `format_executor_result`:

```python
def _step_id(step: Any) -> str:
    if isinstance(step, dict):
        return str(step.get("step_id") or step.get("description") or "step")
    return str(step)


def build_success_step_update(
    *,
    state: dict[str, Any],
    step: Any,
    remaining_plan: list[Any],
    result: str,
    evidence_records: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence_items = []
    events = list(state.get("events", []))
    for record in evidence_records:
        evidence = {
            "step_id": _step_id(step),
            "tool_name": record.get("tool_name", ""),
            "evidence_id": record.get("evidence_id", ""),
            "status": "completed" if record.get("success", True) else "failed",
            "summary": record.get("summary", ""),
            "source": record.get("source", ""),
        }
        evidence_items.append(evidence)
        events.append(
            make_tool_event(
                agent="evidence_collector",
                tool=evidence["tool_name"],
                status=evidence["status"],
                evidence_id=evidence["evidence_id"],
                summary=evidence["summary"],
                payload=evidence,
            )
        )

    if not evidence_items:
        evidence_items.append(
            {
                "step_id": _step_id(step),
                "tool_name": "",
                "evidence_id": "",
                "status": "completed",
                "summary": result,
                "source": "llm",
            }
        )

    return {
        "plan": remaining_plan,
        "past_steps": [
            {
                "step_id": _step_id(step),
                "description": plan_step_text(step),
                "status": "completed",
                "result": result,
            }
        ],
        "evidence": evidence_items,
        "events": events,
    }


def build_failed_step_update(
    *,
    state: dict[str, Any],
    step: Any,
    remaining_plan: list[Any],
    error: Exception,
) -> dict[str, Any]:
    summary = f"Step execution failed: {error}"
    evidence = {
        "step_id": _step_id(step),
        "tool_name": "",
        "evidence_id": "",
        "status": "failed",
        "summary": summary,
        "source": "executor",
    }
    event = make_tool_event(
        agent="evidence_collector",
        tool="",
        status="failed",
        evidence_id="",
        summary=summary,
        payload=evidence,
    )
    return {
        "plan": remaining_plan,
        "past_steps": [
            {
                "step_id": _step_id(step),
                "description": plan_step_text(step),
                "status": "failed",
                "result": summary,
            }
        ],
        "evidence": [evidence],
        "events": list(state.get("events", [])) + [event],
    }
```

- [ ] **Step 4: Use helpers inside `executor`**

In `executor`, replace:

```python
task = plan[0]
```

with:

```python
task, remaining_plan = pop_next_plan_step(plan)
if task is None:
    logger.info("Plan is empty; executor skipped")
    return {}
```

Use `plan_step_text(task)` when building the HumanMessage:

```python
HumanMessage(content=f"Please execute this investigation step: {plan_step_text(task)}")
```

Track `evidence_records` before tool execution:

```python
evidence_records: list[dict[str, Any]] = []
```

After `build_persistent_tool_evidence(...)`, assign to that variable. Replace the successful return with:

```python
return build_success_step_update(
    state=state,
    step=task,
    remaining_plan=remaining_plan,
    result=result,
    evidence_records=evidence_records,
)
```

Replace the exception return with:

```python
return build_failed_step_update(
    state=state,
    step=task,
    remaining_plan=remaining_plan,
    error=e,
)
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
python -m pytest tests/test_aiops_evidence.py tests/test_aiops_evidence_collector.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/agent/aiops/executor.py tests/test_aiops_evidence_collector.py
git commit -m "feat: emit oncall evidence collector events"
```

---

### Task 6: Diagnosis Agent And Loop Routing

**Files:**
- Create: `app/agent/aiops/diagnosis.py`
- Modify: `app/agent/aiops/__init__.py`
- Test: `tests/test_aiops_diagnosis_agent.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_aiops_diagnosis_agent.py`:

```python
import pytest

from app.agent.aiops.diagnosis import route_after_diagnosis
from app.agent.aiops import diagnosis as diagnosis_node


def test_route_after_diagnosis_goes_to_report_when_ready():
    state = {"diagnosis": {"status": "root_cause_ready"}, "iteration": 1, "max_iterations": 2}

    assert route_after_diagnosis(state) == "reporter"


def test_route_after_diagnosis_goes_to_planner_when_evidence_is_insufficient():
    state = {"diagnosis": {"status": "evidence_insufficient"}, "iteration": 1, "max_iterations": 2}

    assert route_after_diagnosis(state) == "planner"


def test_route_after_diagnosis_goes_to_report_at_max_iterations():
    state = {"diagnosis": {"status": "evidence_insufficient"}, "iteration": 2, "max_iterations": 2}

    assert route_after_diagnosis(state) == "reporter"


@pytest.mark.asyncio
async def test_diagnosis_node_uses_generated_diagnosis(monkeypatch):
    async def fake_generate_diagnosis(state):
        return {
            "status": "root_cause_ready",
            "root_cause_candidates": [{"cause": "DB saturation", "confidence": 0.8}],
            "missing_evidence": [],
            "next_focus": "",
            "confidence": 0.8,
        }

    monkeypatch.setattr("app.agent.aiops.diagnosis.generate_diagnosis", fake_generate_diagnosis)

    update = await diagnosis_node({"events": [], "iteration": 0, "max_iterations": 2})

    assert update["diagnosis"]["status"] == "root_cause_ready"
    assert update["iteration"] == 1
    assert update["events"][-1]["type"] == "decision_event"


@pytest.mark.asyncio
async def test_diagnosis_node_falls_back_to_insufficient_evidence(monkeypatch):
    async def fake_generate_diagnosis(state):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr("app.agent.aiops.diagnosis.generate_diagnosis", fake_generate_diagnosis)

    update = await diagnosis_node({"events": [], "iteration": 0, "max_iterations": 2})

    assert update["diagnosis"]["status"] == "evidence_insufficient"
    assert update["events"][-1]["status"] == "evidence_insufficient"
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
python -m pytest tests/test_aiops_diagnosis_agent.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `diagnosis`.

- [ ] **Step 3: Add Diagnosis Agent**

Create `app/agent/aiops/diagnosis.py`:

```python
"""Diagnosis Agent and loop routing for OnCall workflows."""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_qwq import ChatQwen
from loguru import logger
from pydantic import BaseModel, Field

from app.config import config

from .events import make_decision_event


class RootCauseCandidate(BaseModel):
    cause: str
    confidence: float = 0.0
    supporting_evidence_ids: list[str] = Field(default_factory=list)


class DiagnosisResult(BaseModel):
    status: Literal["evidence_insufficient", "root_cause_ready"]
    root_cause_candidates: list[RootCauseCandidate] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    next_focus: str = ""
    confidence: float = 0.0


def route_after_diagnosis(state: dict[str, Any]) -> str:
    diagnosis = state.get("diagnosis", {})
    status = diagnosis.get("status")
    iteration = int(state.get("iteration", 0))
    max_iterations = int(state.get("max_iterations", 2))
    if status == "root_cause_ready":
        return "reporter"
    if iteration >= max_iterations:
        return "reporter"
    return "planner"


async def generate_diagnosis(state: dict[str, Any]) -> dict[str, Any]:
    model = ChatQwen(
        model=config.rag_model,
        api_key=config.dashscope_api_key,
        temperature=0,
        streaming=False,
    )
    classifier = model.with_structured_output(DiagnosisResult)
    result = await classifier.ainvoke(
        [
            SystemMessage(
                content=(
                    "You are an OnCall diagnosis agent. Decide whether evidence is sufficient. "
                    "Return evidence_insufficient when important evidence is missing. "
                    "Return root_cause_ready only when evidence supports the conclusion."
                )
            ),
            HumanMessage(content=f"Incident: {state.get('incident', {})}"),
            HumanMessage(content=f"Evidence: {state.get('evidence', [])}"),
            HumanMessage(content=f"Past steps: {state.get('past_steps', [])}"),
        ]
    )
    if isinstance(result, DiagnosisResult):
        return result.model_dump()
    return DiagnosisResult.model_validate(result).model_dump()


async def diagnosis(state: dict[str, Any]) -> dict[str, Any]:
    try:
        result = await generate_diagnosis(state)
    except Exception as exc:
        logger.warning(f"Diagnosis Agent degraded to insufficient evidence: {exc}")
        result = {
            "status": "evidence_insufficient",
            "root_cause_candidates": [],
            "missing_evidence": ["Diagnosis model was unavailable."],
            "next_focus": "collect metrics, logs, and relevant runbook evidence",
            "confidence": 0.0,
        }

    next_iteration = int(state.get("iteration", 0)) + 1
    summary = (
        "Root cause is ready."
        if result.get("status") == "root_cause_ready"
        else result.get("next_focus") or "More evidence is needed."
    )
    event = make_decision_event(
        agent="diagnosis",
        status=str(result.get("status", "evidence_insufficient")),
        summary=summary,
        payload={"diagnosis": result, "iteration": next_iteration},
    )
    return {
        "diagnosis": result,
        "iteration": next_iteration,
        "events": list(state.get("events", [])) + [event],
    }
```

- [ ] **Step 4: Export Diagnosis Agent**

Modify `app/agent/aiops/__init__.py`:

```python
from .diagnosis import diagnosis
from .executor import executor
from .planner import planner
from .replanner import replanner
from .reporter import reporter
from .state import OnCallState, PlanExecuteState
from .triage import triage

__all__ = [
    "OnCallState",
    "PlanExecuteState",
    "diagnosis",
    "planner",
    "executor",
    "replanner",
    "reporter",
    "triage",
]
```

- [ ] **Step 5: Run tests**

Run:

```bash
python -m pytest tests/test_aiops_diagnosis_agent.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/agent/aiops/diagnosis.py app/agent/aiops/__init__.py tests/test_aiops_diagnosis_agent.py
git commit -m "feat: add oncall diagnosis loop agent"
```

---

### Task 7: Coordinator Graph In AIOpsService

**Files:**
- Modify: `app/services/aiops_service.py`
- Test: `tests/test_aiops_coordinator_graph.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_aiops_coordinator_graph.py`:

```python
from app.services.aiops_service import (
    NODE_DIAGNOSIS,
    NODE_EXECUTOR,
    NODE_PLANNER,
    NODE_REPORTER,
    NODE_TRIAGE,
    AIOpsService,
)


class _FakeMemoryService:
    def create_case(self, session_id, user_input):
        return "case-1"

    def update_case_plan(self, case_id, plan):
        self.updated_plan = plan

    def complete_case(self, case_id, executed_steps, final_report):
        self.completed = {
            "case_id": case_id,
            "executed_steps": executed_steps,
            "final_report": final_report,
        }

    def fail_case(self, case_id, error_message):
        self.failed = {"case_id": case_id, "error_message": error_message}


def test_initial_oncall_state_contains_loop_and_event_fields():
    service = AIOpsService(memory_service=_FakeMemoryService(), checkpointer=None)

    state = service._build_initial_state(
        user_input="checkout-api slow",
        session_id="s1",
        case_id="case-1",
    )

    assert state["input"] == "checkout-api slow"
    assert state["session_id"] == "s1"
    assert state["case_id"] == "case-1"
    assert state["plan"] == []
    assert state["past_steps"] == []
    assert state["evidence"] == []
    assert state["iteration"] == 0
    assert state["max_iterations"] == 2
    assert state["events"] == []


def test_node_constants_describe_coordinator_graph():
    assert NODE_TRIAGE == "triage"
    assert NODE_PLANNER == "planner"
    assert NODE_EXECUTOR == "executor"
    assert NODE_DIAGNOSIS == "diagnosis"
    assert NODE_REPORTER == "reporter"
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
python -m pytest tests/test_aiops_coordinator_graph.py -q
```

Expected: FAIL because new node constants and `_build_initial_state` do not exist.

- [ ] **Step 3: Update imports and node constants**

In `app/services/aiops_service.py`, replace agent imports with:

```python
from app.agent.aiops import OnCallState, diagnosis, executor, planner, reporter, triage
from app.agent.aiops.diagnosis import route_after_diagnosis
```

Replace node constants with:

```python
NODE_TRIAGE = "triage"
NODE_PLANNER = "planner"
NODE_EXECUTOR = "executor"
NODE_DIAGNOSIS = "diagnosis"
NODE_REPORTER = "reporter"
```

- [ ] **Step 4: Add initial state builder**

Add this method to `AIOpsService`:

```python
def _build_initial_state(
    self,
    *,
    user_input: str,
    session_id: str,
    case_id: str,
) -> OnCallState:
    return {
        "input": user_input,
        "session_id": session_id,
        "case_id": case_id,
        "route": "aiops",
        "route_reason": "",
        "incident": {},
        "plan": [],
        "past_steps": [],
        "evidence": [],
        "diagnosis": {},
        "response": "",
        "iteration": 0,
        "max_iterations": 2,
        "events": [],
    }
```

- [ ] **Step 5: Replace `_build_graph` with coordinator graph**

Use this graph shape inside `_build_graph`:

```python
workflow = StateGraph(OnCallState)
workflow.add_node(NODE_TRIAGE, triage)
workflow.add_node(NODE_PLANNER, planner)
workflow.add_node(NODE_EXECUTOR, executor)
workflow.add_node(NODE_DIAGNOSIS, diagnosis)
workflow.add_node(NODE_REPORTER, reporter)

workflow.set_entry_point(NODE_TRIAGE)
workflow.add_edge(NODE_TRIAGE, NODE_PLANNER)
workflow.add_edge(NODE_PLANNER, NODE_EXECUTOR)
workflow.add_edge(NODE_EXECUTOR, NODE_DIAGNOSIS)
workflow.add_conditional_edges(
    NODE_DIAGNOSIS,
    route_after_diagnosis,
    {
        NODE_PLANNER: NODE_PLANNER,
        NODE_REPORTER: NODE_REPORTER,
    },
)
workflow.add_edge(NODE_REPORTER, END)

compiled_graph = workflow.compile(checkpointer=self.checkpointer)
```

- [ ] **Step 6: Use `_build_initial_state` in `execute`**

Replace the current `initial_state` literal with:

```python
initial_state = self._build_initial_state(
    user_input=user_input,
    session_id=session_id,
    case_id=case_id,
)
```

When reading the final response, also read events:

```python
final_events = final_values.get("events", [])
```

Include events in the complete event:

```python
yield {
    "type": "complete",
    "stage": "complete",
    "message": "任务执行完成",
    "case_id": case_id,
    "response": final_response,
    "events": final_events,
}
```

- [ ] **Step 7: Update event formatting**

In the stream loop, before legacy formatting, pass normalized events through:

```python
for normalized_event in node_output.get("events", []) if isinstance(node_output, dict) else []:
    yield normalized_event
```

Keep existing `_format_planner_event`, `_format_executor_event`, and `_format_replanner_event` calls only for backward compatibility. Map `NODE_DIAGNOSIS` to a status event and `NODE_REPORTER` to a report event:

```python
elif node_name == NODE_DIAGNOSIS:
    yield {
        "type": "status",
        "stage": "diagnosis",
        "message": "诊断判断完成",
        "diagnosis": node_output.get("diagnosis", {}) if node_output else {},
    }
elif node_name == NODE_REPORTER:
    yield {
        "type": "report",
        "stage": "final_report",
        "message": "最终报告已生成",
        "report": node_output.get("response", "") if node_output else "",
    }
```

- [ ] **Step 8: Update plan persistence**

Modify `_persist_node_output` so it still persists plans from `NODE_PLANNER`:

```python
if node_name != NODE_PLANNER or not state:
    return
plan = state.get("plan")
if isinstance(plan, list):
    self.memory_service.update_case_plan(case_id, plan)
```

- [ ] **Step 9: Run focused tests**

Run:

```bash
python -m pytest tests/test_aiops_coordinator_graph.py tests/test_aiops_diagnosis_agent.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add app/services/aiops_service.py tests/test_aiops_coordinator_graph.py
git commit -m "feat: coordinate oncall multi-agent graph"
```

---

### Task 8: Assistant API Returns OnCall Events

**Files:**
- Modify: `app/services/router_service.py`
- Modify: `app/api/assistant.py`
- Test: `tests/test_assistant_oncall_events.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_assistant_oncall_events.py`:

```python
import pytest

from app.services.router_service import RouterService, RouteDecision


class _FakeAIOpsService:
    async def execute(self, message, session_id):
        yield {"type": "agent_event", "agent": "triage", "summary": "structured"}
        yield {
            "type": "complete",
            "case_id": "case-1",
            "response": "# Report",
            "events": [{"type": "agent_event", "agent": "report", "summary": "done"}],
        }


class _FakeRagAgentService:
    async def query(self, message, session_id):
        return "rag answer"


@pytest.mark.asyncio
async def test_router_answer_includes_oncall_events(monkeypatch):
    service = RouterService(
        semantic_router=lambda message: RouteDecision(route="aiops", reason="test_aiops")
    )
    monkeypatch.setattr("app.services.router_service.aiops_service", _FakeAIOpsService())
    monkeypatch.setattr("app.services.router_service.rag_agent_service", _FakeRagAgentService())

    response = await service.answer("checkout-api slow", session_id="s1")

    assert response["success"] is True
    assert response["route"] == "aiops"
    assert response["route_reason"] == "test_aiops"
    assert response["case_id"] == "case-1"
    assert response["answer"] == "# Report"
    assert response["events"] == [
        {"type": "agent_event", "agent": "triage", "summary": "structured"},
        {"type": "agent_event", "agent": "report", "summary": "done"},
    ]


@pytest.mark.asyncio
async def test_router_answer_keeps_rag_response_without_events(monkeypatch):
    service = RouterService(
        semantic_router=lambda message: RouteDecision(route="rag", reason="test_rag")
    )
    monkeypatch.setattr("app.services.router_service.rag_agent_service", _FakeRagAgentService())

    response = await service.answer("how to handle slow response", session_id="s1")

    assert response["route"] == "rag"
    assert "events" not in response
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
python -m pytest tests/test_assistant_oncall_events.py -q
```

Expected: FAIL because `RouterService.answer` does not collect events.

- [ ] **Step 3: Collect AIOps events in RouterService**

In `app/services/router_service.py`, inside the `decision.route == "aiops"` branch, replace the event loop with:

```python
final_answer = ""
case_id = ""
events: list[dict[str, object]] = []
async for event in aiops_service.execute(message, session_id=session_id):
    event_type = event.get("type")
    if event_type in {"agent_event", "tool_event", "decision_event"}:
        events.append(dict(event))
    if event_type == "complete":
        case_id = str(event.get("case_id") or "")
        final_answer = str(event.get("response") or event.get("message") or "")
        for item in event.get("events") or []:
            if isinstance(item, dict):
                events.append(dict(item))
    elif event_type == "error":
        return {
            "success": False,
            "route": "aiops",
            "route_reason": decision.reason,
            "case_id": str(event.get("case_id") or ""),
            "answer": None,
            "events": events,
            "errorMessage": str(event.get("message") or event.get("response") or ""),
        }
return {
    "success": True,
    "route": "aiops",
    "route_reason": decision.reason,
    "case_id": case_id,
    "answer": final_answer,
    "events": events,
    "errorMessage": None,
}
```

Leave the RAG branch unchanged so existing RAG responses do not gain an `events` field.

- [ ] **Step 4: Run tests**

Run:

```bash
python -m pytest tests/test_assistant_oncall_events.py tests/test_assistant_api.py tests/test_router_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/router_service.py tests/test_assistant_oncall_events.py
git commit -m "feat: return oncall timeline events from assistant"
```

---

### Task 9: API Streaming Compatibility And Completion Fields

**Files:**
- Modify: `app/api/aiops.py`
- Test: `tests/test_aiops_stream_events.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_aiops_stream_events.py`:

```python
import json

import pytest

from app.api import aiops as aiops_api

SESSION_HEADERS = {"X-Session-Owner": "owner-a"}


class _FakeAIOpsService:
    async def diagnose(self, session_id="default"):
        yield {
            "type": "agent_event",
            "agent": "triage",
            "stage": "triage",
            "status": "completed",
            "summary": "Incident structured",
            "payload": {},
        }
        yield {
            "type": "complete",
            "stage": "diagnosis_complete",
            "message": "done",
            "diagnosis": {"status": "completed", "case_id": "case-1", "report": "# Report"},
        }


@pytest.mark.asyncio
async def test_aiops_stream_passes_normalized_events(monkeypatch, api_client):
    monkeypatch.setattr(aiops_api, "aiops_service", _FakeAIOpsService(), raising=False)

    response = await api_client.post(
        "/api/aiops",
        headers=SESSION_HEADERS,
        json={"session_id": "s1"},
    )

    assert response.status_code == 200
    body = response.text
    assert "agent_event" in body
    assert "triage" in body
    assert "diagnosis_complete" in body
```

- [ ] **Step 2: Run the test**

Run:

```bash
python -m pytest tests/test_aiops_stream_events.py -q
```

Expected: PASS if the current SSE pass-through already handles normalized events. If it fails because the response body format differs, update only the assertion parsing, not production code.

- [ ] **Step 3: Update API docstring event list**

In `app/api/aiops.py`, extend the endpoint docstring to list normalized event types:

```text
7. `agent_event` - normalized agent timeline event
8. `tool_event` - normalized evidence/tool timeline event
9. `decision_event` - normalized diagnosis loop decision event
```

Do not change the SSE envelope. Continue yielding:

```python
yield {
    "event": "message",
    "data": json.dumps(event, ensure_ascii=False),
}
```

- [ ] **Step 4: Run tests**

Run:

```bash
python -m pytest tests/test_aiops_stream_events.py tests/test_aiops_feedback_api.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api/aiops.py tests/test_aiops_stream_events.py
git commit -m "test: cover oncall normalized stream events"
```

---

### Task 10: End-To-End Deterministic Coordinator Test

**Files:**
- Modify: `app/services/aiops_service.py`
- Test: `tests/test_aiops_coordinator_e2e.py`

- [ ] **Step 1: Write deterministic fake-node graph test**

Create `tests/test_aiops_coordinator_e2e.py`:

```python
import pytest

from app.services.aiops_service import AIOpsService


class _FakeMemoryService:
    def __init__(self):
        self.completed = None

    def create_case(self, session_id, user_input):
        return "case-1"

    def update_case_plan(self, case_id, plan):
        pass

    def complete_case(self, case_id, executed_steps, final_report):
        self.completed = {
            "case_id": case_id,
            "executed_steps": executed_steps,
            "final_report": final_report,
        }

    def fail_case(self, case_id, error_message):
        raise AssertionError(error_message)


@pytest.mark.asyncio
async def test_aiops_execute_emits_complete_event_with_events(monkeypatch, tmp_path):
    from langgraph.checkpoint.memory import MemorySaver

    async def fake_triage(state):
        return {
            "incident": {"incident_type": "slow_response", "service_name": "checkout-api"},
            "events": [{"type": "agent_event", "agent": "triage", "summary": "structured"}],
        }

    async def fake_planner(state):
        return {
            "plan": [{"step_id": "plan-1", "description": "check metrics"}],
            "events": state.get("events", []) + [
                {"type": "agent_event", "agent": "planner", "summary": "planned"}
            ],
        }

    async def fake_executor(state):
        return {
            "plan": [],
            "past_steps": [{"step_id": "plan-1", "status": "completed", "result": "latency high"}],
            "evidence": [{"evidence_id": "ev-1", "status": "completed", "summary": "latency high"}],
            "events": state.get("events", []) + [
                {"type": "tool_event", "agent": "evidence_collector", "evidence_id": "ev-1"}
            ],
        }

    async def fake_diagnosis(state):
        return {
            "diagnosis": {"status": "root_cause_ready"},
            "iteration": 1,
            "events": state.get("events", []) + [
                {"type": "decision_event", "agent": "diagnosis", "status": "root_cause_ready"}
            ],
        }

    async def fake_reporter(state):
        return {
            "response": "# Report",
            "events": state.get("events", []) + [
                {"type": "agent_event", "agent": "report", "summary": "reported"}
            ],
        }

    monkeypatch.setattr("app.services.aiops_service.triage", fake_triage)
    monkeypatch.setattr("app.services.aiops_service.planner", fake_planner)
    monkeypatch.setattr("app.services.aiops_service.executor", fake_executor)
    monkeypatch.setattr("app.services.aiops_service.diagnosis", fake_diagnosis)
    monkeypatch.setattr("app.services.aiops_service.reporter", fake_reporter)

    memory = _FakeMemoryService()
    service = AIOpsService(memory_service=memory, checkpointer=MemorySaver())

    events = [event async for event in service.execute("checkout-api slow", session_id="s1")]

    complete = events[-1]
    assert complete["type"] == "complete"
    assert complete["case_id"] == "case-1"
    assert complete["response"] == "# Report"
    assert any(event.get("agent") == "triage" for event in complete["events"])
    assert memory.completed["final_report"] == "# Report"
```

- [ ] **Step 2: Run the test and verify failure if graph wiring is incomplete**

Run:

```bash
python -m pytest tests/test_aiops_coordinator_e2e.py -q
```

Expected after Task 7: PASS. If it fails with the real nodes being used instead of monkeypatched nodes, move `_build_graph()` invocation later so monkeypatches are visible before graph compilation in this test.

- [ ] **Step 3: Fix graph initialization if needed**

If the test fails because `AIOpsService.__init__` eagerly builds the graph before monkeypatching, keep the existing lazy path and instantiate with `checkpointer=None`, then set `service.checkpointer = MemorySaver()` before `execute`. The preferred production behavior remains lazy initialization through `_initialize_graph`.

- [ ] **Step 4: Run tests**

Run:

```bash
python -m pytest tests/test_aiops_coordinator_e2e.py tests/test_aiops_coordinator_graph.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/aiops_service.py tests/test_aiops_coordinator_e2e.py
git commit -m "test: verify oncall coordinator event completion"
```

---

### Task 11: Regression And Documentation Check

**Files:**
- Modify: `README.md` only if the current API section does not mention `/api/assistant` returning `events`.
- No new production files unless tests expose a real compatibility issue.

- [ ] **Step 1: Run targeted regression**

Run:

```bash
python -m pytest tests/test_router_service.py tests/test_assistant_api.py tests/test_chat_api.py tests/test_aiops_feedback_api.py tests/test_aiops_evidence.py -q
```

Expected: PASS.

- [ ] **Step 2: Run all new OnCall tests**

Run:

```bash
python -m pytest tests/test_aiops_events_state.py tests/test_aiops_triage.py tests/test_aiops_reporter.py tests/test_aiops_plan_utils.py tests/test_aiops_evidence_collector.py tests/test_aiops_diagnosis_agent.py tests/test_aiops_coordinator_graph.py tests/test_assistant_oncall_events.py tests/test_aiops_stream_events.py tests/test_aiops_coordinator_e2e.py -q
```

Expected: PASS.

- [ ] **Step 3: Run full test suite**

Run:

```bash
python -m pytest
```

Expected: PASS. If tests requiring live Milvus or DashScope fail because services are unavailable, record the failing test names and the exact unavailable dependency in the final implementation note.

- [ ] **Step 4: Update README if needed**

If `README.md` does not describe `events` under `/api/assistant`, add this additive response shape:

```json
{
  "success": true,
  "route": "aiops",
  "route_reason": "llm_semantic_aiops",
  "case_id": "case-xxx",
  "answer": "# OnCall Diagnosis Report...",
  "events": [
    {
      "type": "agent_event",
      "agent": "triage",
      "stage": "triage",
      "status": "completed",
      "summary": "Structured incident",
      "payload": {}
    }
  ],
  "errorMessage": null
}
```

- [ ] **Step 5: Commit docs if changed**

```bash
git add README.md
git commit -m "docs: describe oncall assistant events"
```

Skip this commit when `README.md` already contains equivalent information.

- [ ] **Step 6: Final verification status**

Capture:

```bash
git status --short
git log -5 --oneline
```

Expected: only intentional uncommitted files remain. Do not revert unrelated pre-existing changes.

---

## Self-Review

Spec coverage:

- Six agents are covered by Tasks 2, 3, 4, 5, 6, 7, and 8.
- Supervisor orchestration is covered by Task 7 and Task 10.
- Evidence-insufficient loop and `max_iterations` are covered by Task 6 and Task 7.
- Normalized timeline events are covered by Task 1, Task 5, Task 7, Task 8, and Task 9.
- API completion fields are covered by Task 8 and Task 9.
- Error degradation is covered by Task 2, Task 3, Task 5, and Task 6.
- Regression expectations are covered by Task 11.

Placeholder scan:

- The plan contains no open-ended implementation placeholders.
- Each code-changing task includes concrete tests, exact commands, and code snippets.

Type consistency:

- Event dictionaries consistently use `type`, `agent`, `status`, `summary`, and `payload`.
- Plan steps consistently use `step_id`, `description`, `tool_category`, and `expected_evidence`.
- Coordinator node names consistently use `triage`, `planner`, `executor`, `diagnosis`, and `reporter`.
