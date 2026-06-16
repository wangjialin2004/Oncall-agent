# Agent Gateway React UI Design

## Context

The existing system already has a working OnCall multi-agent backend flow under `app/`.
Recent work added normalized timeline events such as `agent_event`, `tool_event`, and
`decision_event`, plus final diagnosis reports and feedback persistence.

The current browser UI is still the legacy static frontend in `static/`. It mixes chat
behavior, upload behavior, AIOps rendering, timeline rendering, and feedback handling in
large JavaScript and CSS files. The new requirement is to separate the frontend and
backend application layers from the existing agent system and show the multi-agent
collaboration process in the frontend as it happens.

## Goals

- Create a new `frontend/` React + TypeScript application.
- Create a new `backend/` FastAPI Agent Gateway application.
- Keep the existing `app/` agent system as the reusable core; do not move or rewrite the
  existing agent internals in this iteration.
- Let the frontend choose an agent mode: `auto`, `rag`, or `oncall`.
- Let the backend route `auto` requests to the correct agent and stream the selected
  agent execution back to the frontend.
- Display the OnCall multi-agent process in real time with a chat-first layout and a
  right-side collaboration panel.

## Non-Goals

- Do not keep legacy API compatibility as the primary design constraint. The new
  frontend should target the new Agent Gateway API.
- Do not delete the legacy `static/` frontend in the first iteration.
- Do not add persistent run history, run replay, or a background event bus in this
  iteration.
- Do not implement true backend task cancellation in the first iteration. The frontend
  may close the SSE connection and mark a run as cancelled locally.
- Do not duplicate or relocate the existing `app/agent` implementation.

## Architecture

The repository will have three clear layers:

```text
frontend/          React + TypeScript + Vite frontend
backend/           FastAPI Agent Gateway for frontend-facing APIs
app/               Existing agent core, services, tools, and models
static/            Legacy static frontend retained during migration
```

`backend/` imports the existing `app.services` and `app.agent` capabilities. It owns the
new API shape, route selection, event normalization, CORS setup, and SSE streaming.

Primary request flow:

```text
React UI
  -> POST /api/agent/stream { session_id, message, mode }
  -> backend router resolves mode: auto / rag / oncall
  -> backend invokes the selected existing service
  -> backend streams normalized SSE events
  -> frontend renders chat output and the right-side agent process panel
```

The design separates the new application surfaces from the existing agent system while
preserving the already validated agent logic.

## Frontend Experience

Use the approved layout: chat-first workspace with a right-side real-time collaboration
panel.

Main component structure:

```text
AppShell
  Sidebar
    New session
    History list
    Mode entry points
  ChatWorkspace
    ChatHeader
    MessageList
    Composer
  AgentProcessPanel
    RouteCard
    StageTimeline
    EventStream
    EvidenceDrawer
    ReportCard
    FeedbackForm
```

The user flow:

1. The user selects `auto`, `rag`, or `oncall` and sends a message.
2. The chat area immediately shows the user message and a running assistant placeholder.
3. The process panel clears the previous run and opens the current run view.
4. As SSE events arrive, the panel updates route, stage, tool, decision, evidence, and
   report sections in real time.
5. On `complete`, the chat area shows the final answer and the panel shows final status,
   report, case id, and feedback controls when available.
6. On `error`, the UI keeps already received events visible and shows a clear failure
   state.

The UI should not be a marketing page. The first screen is the usable chat and agent
process workspace.

## Backend API

The main frontend-facing endpoint is:

```http
POST /api/agent/stream
Content-Type: application/json
Accept: text/event-stream
```

Request body:

```json
{
  "session_id": "session-123",
  "message": "Diagnose slow checkout-api responses",
  "mode": "auto"
}
```

Supported modes:

```text
auto     backend chooses the agent route
rag      knowledge/chat agent
oncall   OnCall multi-agent diagnosis
```

The backend sends SSE `message` frames whose `data` field is JSON.

Unified event types:

```text
route_selected   backend selected the effective agent route
agent_event      normalized agent stage event
tool_event       tool call or evidence collection event
decision_event   diagnosis-loop decision event
content          streamed RAG/chat text chunk
report           final OnCall report
complete         run completed with final answer/case/events summary
error            run failed
```

The OnCall route should pass through the existing normalized event shape where possible.
The RAG route should wrap streamed answer chunks as `content` events and finish with
`complete`.

## Event Data

Frontend run state:

```text
run_id
session_id
mode
route
status: idle / running / completed / error / cancelled
events[]
answer
case_id
error
```

The backend should emit `route_selected` first for every accepted request. Frontend stage
state should be derived from received events instead of hard-coded sequence timing.

For OnCall events, the frontend should support:

- `agent`
- `stage`
- `status`
- `summary`
- `tool`
- `evidence_id`
- `payload`

Unknown event fields should remain inspectable in the detail drawer so future agent
events do not require immediate UI rewrites.

## Error Handling

- Route selection failure emits `error` with a clear message.
- Agent execution failure emits `error` and keeps prior events visible.
- SSE interruption marks the current run as disconnected and lets the user submit again.
- OnCall timeout emits a degraded `agent_event` and a degraded report when the backend can
  produce one.
- Feedback submission errors affect only the feedback form and do not erase the run
  result.

The first iteration may implement frontend-side stop by closing the SSE connection and
marking the run as cancelled locally.

## Testing Strategy

Backend tests:

- `auto`, `rag`, and `oncall` mode routing.
- SSE event ordering starts with `route_selected`.
- OnCall route forwards `agent_event`, `tool_event`, `decision_event`, `report`, and
  `complete`.
- RAG route emits `content` and `complete`.
- Error route emits `error` without requiring live external services.

Frontend tests:

- Mode selector sends `auto`, `rag`, or `oncall`.
- Sending a message creates a running run state.
- Incoming events update the process panel.
- `complete` appends the final assistant answer.
- `error` preserves prior events and shows an error state.
- Feedback form posts the selected case id and session id.

Browser checks:

- Desktop layout shows chat plus the right-side process panel.
- Narrow layout remains usable without text overflow.
- Realtime events append without shifting fixed controls.
- The primary message send path works end to end against the new backend.

## Rollout

First iteration:

- Add `frontend/`.
- Add `backend/`.
- Implement the new Agent Gateway stream endpoint.
- Wire the React UI to the new endpoint.
- Keep `static/` available but stop treating it as the primary frontend.

Later iterations can add persistent run history, run replay, richer graph visualization,
server-side cancellation, and stronger event storage.

## Open Decisions Resolved

- Realtime display is required.
- The new backend and frontend are separate from the existing agent system.
- The existing `app/` agent core is reused rather than moved.
- The approved UI layout is chat-first with a right-side real-time collaboration panel.
- The frontend offers `auto`, `rag`, and `oncall`; the backend handles route fallback for
  `auto`.
- The first implementation targets the new Agent Gateway API instead of preserving legacy
  frontend API compatibility.
