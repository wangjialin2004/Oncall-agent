# OnCall Multi-Agent Diagnosis Design

Date: 2026-06-15

## Goal

Build a multi-agent collaboration system for intelligent OnCall fault diagnosis.

The system should take a user's incident description, route it into an OnCall diagnosis workflow, structure the incident, plan evidence collection, call monitoring/log/RAG tools, analyze root-cause candidates, and produce a visible diagnosis timeline plus a final report.

This design uses the recommended Supervisor orchestration mode. A coordinator controls the workflow and calls specialized agents in a predictable order. The workflow may loop back for more evidence when the diagnosis is under-supported.

## Existing Context

The project already has several useful foundations:

- `RouterService` routes requests between RAG and AIOps paths.
- `AIOpsService` runs a LangGraph Plan-Execute-Replan workflow.
- `planner.py`, `executor.py`, and `replanner.py` already separate planning, tool execution, and decision-making responsibilities.
- MCP monitor and log tools can collect operational evidence.
- RAG retrieval is exposed through local agent tools.
- Diagnosis cases, evidence, feedback, and LangGraph checkpoints have persistence surfaces.

The missing layer is explicit multi-agent collaboration. Today the system is mostly a Plan-Execute-Replan graph. The new design gives each stage an agent identity, a clear input/output contract, and a uniform event stream that the frontend can render as a diagnosis process.

## Non-Goals

- Do not build automatic mutating remediation in this version.
- Do not replace existing RAG chat endpoints.
- Do not let agents freely debate in a shared conversation without workflow control.
- Do not require Prometheus, cloud CLS, or external production systems for unit tests.
- Do not remove the current `planner`, `executor`, or `replanner` modules; evolve them where possible.

## Agent Set

The first version uses six agents.

### Router Agent

Responsibility:

- Decide whether the user's request is an OnCall diagnosis request or a normal RAG/chat request.
- Return a route and a short route reason.

Input:

- Raw user message.
- Session ID.

Output:

```json
{
  "route": "aiops",
  "route_reason": "matched incident diagnosis intent"
}
```

Implementation mapping:

- Keep `app/services/router_service.py` as the Router Agent entry.
- Continue using fast deterministic checks first.
- Use semantic routing only when deterministic checks are not enough.

### Triage Agent

Responsibility:

- Convert the raw incident description into a structured incident context.
- Identify the fault type, affected service, time window, severity, known symptoms, missing fields, and likely evidence needs.

Input:

- Raw user message.
- Session ID.
- Optional context from previous turns.

Output:

```json
{
  "incident_type": "slow_response",
  "service_name": "checkout-api",
  "time_window": "last_30_minutes",
  "severity": "P1",
  "symptoms": ["requests keep spinning", "latency increased"],
  "missing_fields": ["environment"],
  "evidence_needs": ["metrics", "logs", "knowledge"]
}
```

Implementation mapping:

- Add `app/agent/aiops/triage.py`.
- If triage fails, create a minimal incident from the raw input and mark confidence as low.

### Planner Agent

Responsibility:

- Create a concrete diagnosis plan from the structured incident and current evidence summary.
- Each step must say what to inspect, which tool category to use, and what evidence it expects to obtain.

Input:

- Structured incident.
- Existing evidence summary.
- Current iteration.

Output:

```json
[
  {
    "step_id": "plan-1",
    "description": "Check latency and error-rate metrics for checkout-api in the last 30 minutes.",
    "tool_category": "monitor",
    "expected_evidence": "latency/error-rate trend and anomaly window"
  }
]
```

Implementation mapping:

- Evolve `app/agent/aiops/planner.py`.
- Keep the current fallback planning behavior, but make fallback plans structured and label them as degraded.

### Evidence Collector Agent

Responsibility:

- Execute plan steps by calling available tools.
- Persist tool outputs into the evidence chain.
- Return compact evidence summaries instead of flooding the LLM context with raw outputs.

Input:

- Current plan.
- Incident context.
- Case ID.

Output:

```json
{
  "step_id": "plan-1",
  "status": "completed",
  "tool_name": "query_metrics_alerts",
  "evidence_id": "ev_20260615_001",
  "summary": "P95 latency rose above 3s and error rate increased after 14:20."
}
```

Implementation mapping:

- Evolve `app/agent/aiops/executor.py` into the Evidence Collector Agent.
- A failed tool call becomes failed evidence, not a workflow crash.

### Diagnosis Agent

Responsibility:

- Analyze the collected evidence.
- Decide whether evidence is sufficient.
- Produce root-cause candidates, confidence, missing evidence, and the next investigation direction.

Input:

- Incident context.
- Plan execution history.
- Evidence summaries.

Output:

```json
{
  "status": "evidence_insufficient",
  "root_cause_candidates": [
    {
      "cause": "database connection pool saturation",
      "confidence": 0.62,
      "supporting_evidence_ids": ["ev_20260615_001", "ev_20260615_002"]
    }
  ],
  "missing_evidence": ["database connection pool metrics"],
  "next_focus": "collect DB connection and slow-query evidence"
}
```

Implementation mapping:

- Evolve `app/agent/aiops/replanner.py` into the Diagnosis Agent and loop decision node.
- Keep the current Replan concept, but make the node's contract explicit: either continue evidence collection or finalize.

### Report Agent

Responsibility:

- Generate the final diagnosis report.
- Separate confirmed facts, inferred causes, missing evidence, and recommended actions.
- Package the diagnosis timeline for frontend visualization.

Input:

- Incident context.
- Evidence chain summaries.
- Diagnosis result.
- Agent events.

Output:

```json
{
  "summary": "checkout-api latency increased because database connections appear saturated.",
  "confirmed_facts": [],
  "root_cause": {},
  "recommendations": [],
  "confidence": 0.78,
  "events": []
}
```

Implementation mapping:

- Add `app/agent/aiops/reporter.py`.
- If report generation fails, return a structured fallback summary from the state.

## Collaboration Mode

Use Supervisor orchestration.

The coordinator owns the workflow state and invokes each agent as a LangGraph node:

```text
User Input
  -> Router Agent
  -> Triage Agent
  -> Planner Agent
  -> Evidence Collector Agent
  -> Diagnosis Agent
      -> evidence_insufficient: Planner Agent / Evidence Collector Agent
      -> root_cause_ready: Report Agent
      -> max_iterations_reached: Report Agent
  -> Report Agent
```

This mode fits OnCall diagnosis because the workflow is evidence-driven and phase-based. The system needs control, auditability, and loop limits more than free-form agent discussion.

## State Model

Extend the current AIOps state into an OnCall diagnosis state:

```python
class OnCallState(TypedDict):
    input: str
    session_id: str
    case_id: str

    route: str
    route_reason: str

    incident: dict
    plan: list[dict]
    past_steps: list[dict]
    evidence: list[dict]

    diagnosis: dict
    response: str

    iteration: int
    max_iterations: int
    events: list[dict]
```

Field meanings:

- `input`: original user request.
- `session_id`: conversation/session scope.
- `case_id`: diagnosis case ID for evidence persistence.
- `route` and `route_reason`: routing result for observability.
- `incident`: structured triage output.
- `plan`: current investigation plan.
- `past_steps`: executed plan steps and statuses.
- `evidence`: compact evidence references and summaries.
- `diagnosis`: latest root-cause analysis and loop decision.
- `response`: final report text or structured report payload.
- `iteration`: number of diagnosis loops already completed.
- `max_iterations`: hard limit to prevent infinite loops.
- `events`: frontend-visible timeline events.

## Loop Rules

The Diagnosis Agent controls the loop:

```text
If evidence is sufficient:
  go to Report Agent

If evidence is insufficient and iteration < max_iterations:
  ask Planner Agent for a supplemental plan
  go back to Evidence Collector Agent

If evidence is insufficient and iteration >= max_iterations:
  go to Report Agent with a low-confidence diagnosis
```

Default `max_iterations` should be 2 for the first implementation. This allows one initial investigation plus one supplemental pass.

## Event Stream

Every agent node emits a normalized event.

Agent event:

```json
{
  "type": "agent_event",
  "agent": "planner",
  "stage": "planning",
  "status": "completed",
  "summary": "Generated 4 investigation steps.",
  "payload": {}
}
```

Tool event:

```json
{
  "type": "tool_event",
  "agent": "evidence_collector",
  "tool": "query_metrics_alerts",
  "status": "completed",
  "evidence_id": "ev_20260615_001",
  "summary": "CPU stayed normal while latency rose sharply."
}
```

Decision event:

```json
{
  "type": "decision_event",
  "agent": "diagnosis",
  "status": "evidence_insufficient",
  "summary": "Need database connection evidence before finalizing root cause.",
  "payload": {
    "next_focus": "database connection pool"
  }
}
```

The API should stream these events through the existing AIOps event path and include the final event list in the completed response when using `/api/assistant`.

## API Behavior

`POST /api/assistant` keeps routing between RAG and OnCall.

For an OnCall request, the response data should include:

```json
{
  "success": true,
  "route": "aiops",
  "route_reason": "llm_semantic_aiops",
  "case_id": "case_xxx",
  "answer": "...final report...",
  "events": [],
  "errorMessage": null
}
```

Existing `/api/aiops` streaming should continue to work and should expose the same normalized event types.

## Error Handling

Use graceful degradation at each stage:

- Router failure: default to RAG unless strong AIOps keywords were already matched.
- Triage failure: create a minimal incident from raw input and mark confidence as low.
- Planner failure: use a structured fallback plan.
- Tool failure: write failed evidence and continue with available evidence.
- Diagnosis uncertainty: request supplemental evidence until `max_iterations` is reached.
- Report failure: return a fallback report from incident, evidence summaries, and diagnosis fields.

Every degraded path should emit an event so the frontend and logs show what happened.

## Testing Strategy

Add focused tests before implementation:

- Router routes incident-like requests into the OnCall path.
- Triage returns a valid incident object for common incident descriptions.
- Planner returns structured plan steps with tool categories and expected evidence.
- Evidence Collector records failed tool calls as failed evidence without crashing the workflow.
- Diagnosis routes back to planning when evidence is insufficient.
- `max_iterations` prevents infinite loops.
- Report includes incident summary, evidence, root-cause section, recommendations, and event timeline.
- `/api/assistant` includes `route_reason`, `case_id`, final answer, and events for OnCall requests.

Unit tests should mock LLM and MCP tool calls. Integration tests can exercise the graph with deterministic fake agent outputs.

## Rollout

Implement in small slices:

1. Add shared state and event schema.
2. Add Triage Agent and Report Agent.
3. Refine Planner, Evidence Collector, and Diagnosis contracts.
4. Update `AIOpsService` graph into the coordinator workflow.
5. Expose normalized events through API responses.
6. Add tests and keep existing RAG and AIOps behavior compatible.

The first release can keep the existing `/api/aiops` route while internally using the new coordinator graph.

## Completion Evidence

The design is complete when:

- The graph has explicit Router, Triage, Planner, Evidence Collector, Diagnosis, and Report responsibilities.
- Evidence-insufficient cases loop once for supplemental collection and then stop at `max_iterations`.
- API responses and streams include normalized timeline events.
- Tests prove routing, loop control, tool-failure handling, and final report structure.
- Existing chat/RAG endpoints continue to pass their current tests.
