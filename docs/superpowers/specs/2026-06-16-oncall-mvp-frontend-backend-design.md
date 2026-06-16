# OnCall MVP Frontend-Backend Design

Date: 2026-06-16

## Goal

Ship a runnable OnCall MVP that connects the existing multi-agent backend to the current static frontend.

The MVP must do three things well:

1. route an incident-like user request into the OnCall path,
2. return a visible diagnosis timeline plus final report,
3. let the user review and submit feedback from the UI.

This is a staging step. It is intentionally smaller than the full multi-agent completion plan already present in the repo.

## Existing State

The repository already has:

- a FastAPI backend with `/api/assistant`, `/api/aiops`, `/api/aiops/feedback`, and feedback query endpoints,
- a router service that can choose between RAG and AIOps,
- an OnCall coordinator service that already emits normalized events,
- a static frontend chat shell in `static/index.html`, `static/app.js`, and `static/styles.css`,
- persisted diagnosis memory and feedback storage.

The main gap is product integration. The backend can produce useful OnCall output, but the frontend still treats AI Ops mostly as a special chat mode instead of a diagnosis workflow with clear stages and feedback.

## Non-Goals

- No full redesign of the app shell.
- No mutating remediation actions.
- No new external monitoring providers.
- No new agent classes beyond what already exists in the backend design.
- No rewrite of the normal RAG chat experience.

## MVP Scope

### Backend

- Ensure `/api/assistant` returns OnCall responses with `route`, `route_reason`, `case_id`, `answer`, and `events`.
- Ensure `/api/aiops` streams the same normalized timeline events already produced by the coordinator.
- Keep fallback behavior in place when the agent graph or LLM path degrades.
- Preserve existing RAG behavior.

### Frontend

- Add an OnCall diagnosis view inside the current static app.
- Render the event timeline in order as events arrive.
- Show the final report clearly, separate from ordinary chat messages.
- Surface `case_id` and feedback submission state.
- Keep the current chat and upload flows intact.

### Tests

- Add focused backend tests for routing, completion payloads, and SSE event shape.
- Add frontend smoke coverage at the behavior level where practical.

## Architecture

Use the current backend as the source of truth.

```text
User input
  -> static frontend
  -> /api/assistant or /api/aiops
  -> router_service
  -> aiops_service coordinator graph
  -> normalized timeline events
  -> final report
  -> frontend timeline + feedback UI
```

The frontend should not infer diagnosis state from ad hoc message text. It should consume structured event objects and the final `answer` payload.

## Backend Design

### Assistant Response Contract

`POST /api/assistant` should return a consistent JSON wrapper whose `data` field includes:

```json
{
  "success": true,
  "route": "aiops",
  "route_reason": "llm_semantic_aiops",
  "case_id": "case-xxx",
  "answer": "# OnCall Diagnosis Report ...",
  "events": [],
  "errorMessage": null
}
```

RAG responses keep the existing shape and do not need to invent empty OnCall fields.

### AIOps Stream Contract

`POST /api/aiops` continues to stream SSE messages, but every event should be compatible with the normalized event types already present in the backend:

- `agent_event`
- `tool_event`
- `decision_event`
- `status`
- `plan`
- `step_complete`
- `report`
- `complete`
- `error`

The stream should remain tolerant of partial failures. If a stage degrades, the frontend still needs enough information to show what happened.

### Service Responsibilities

- `router_service.py` selects the route and collects OnCall events for `/api/assistant`.
- `aiops_service.py` owns the coordinator execution and final completion payload.
- `app/api/aiops.py` keeps the SSE wrapper thin.

## Frontend Design

### Main Screen

Keep the current chat shell, but make AI Ops feel like a diagnosis workflow instead of a generic chat variant.

The screen should include:

- the existing conversation list,
- the current chat thread,
- a visible AI Ops action that starts diagnosis,
- a dedicated diagnosis panel or section that shows:
  - case ID,
  - current status,
  - event timeline,
  - final report,
  - feedback actions.

### Timeline View

Render normalized events as ordered rows.

Each row should show:

- event type,
- agent or tool name,
- short summary,
- status,
- optional details payload when present.

The timeline should update while streaming, not only after completion.

### Report View

The final report should be rendered as a stable, readable block separated from the live timeline.

It should show:

- diagnosis summary,
- root cause,
- evidence notes,
- recommendations,
- confidence if provided,
- case ID for feedback lookup.

### Feedback Flow

Reuse the existing feedback API, but expose it from the UI near the final report.

The user should be able to:

- mark the diagnosis as accepted or not accepted,
- provide actual root cause,
- provide final resolution,
- add a short comment.

Feedback submission should update the UI state immediately and show success or failure clearly.

## Data Flow

1. User enters an incident description.
2. Frontend sends it to `/api/assistant` or `/api/aiops`.
3. Router sends incident-like messages to the OnCall path.
4. Coordinator emits timeline events while it works.
5. Backend returns the final report and case ID.
6. Frontend renders the event timeline and report.
7. User optionally submits feedback tied to the case ID.

The same case ID must be reused for the report and feedback lookup.

## Error Handling

### Backend

- If routing fails, fall back to RAG unless the request clearly looks like an incident.
- If triage fails, build a minimal incident and continue.
- If planner or tool execution fails, emit a degraded event and continue with available evidence.
- If diagnosis cannot reach certainty, return the best available report instead of dropping the request.
- If the final report generation fails, return a structured fallback report.

### Frontend

- If streaming fails, show the last known answer and an error banner.
- If the report is incomplete, keep the timeline visible rather than clearing it.
- If feedback submission fails, preserve the form values so the user can retry.

## Testing

### Backend Tests

Add or update tests to cover:

- OnCall routing through `/api/assistant`,
- event collection in assistant responses,
- SSE event formatting from `/api/aiops`,
- fallback behavior when the OnCall path degrades,
- feedback submission and lookup remain compatible.

### Frontend Tests

Verify at least the following behaviors:

- AI Ops starts a diagnosis flow from the UI,
- timeline events render in order,
- final report displays separately from normal chat,
- feedback submission uses the returned case ID.

### Verification

Before completion, run the targeted test set for the modified backend pieces and smoke-check the frontend in a browser.

## Implementation Order

1. Stabilize backend response payloads for assistant and streaming AIOps.
2. Add frontend diagnosis rendering for timeline and report.
3. Wire feedback UI to the existing feedback endpoints.
4. Add tests for the new integration points.
5. Validate the local app end to end.

## Completion Criteria

The MVP is complete when:

- incident-like requests reliably route to OnCall,
- the frontend shows a live diagnosis timeline,
- the final report is visible and linked to a case ID,
- feedback can be submitted from the UI,
- existing chat and upload flows still work,
- tests cover the new response and event contract.

