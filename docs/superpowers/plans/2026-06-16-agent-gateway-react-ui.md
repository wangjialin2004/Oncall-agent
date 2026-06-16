# Agent Gateway React UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a separated `backend/` FastAPI Agent Gateway and `frontend/` React + TypeScript UI that streams and displays multi-agent execution in real time.

**Architecture:** `backend/` owns frontend-facing routing, event normalization, and SSE streaming while importing existing `app/` agent services. `frontend/` is a Vite React app with a chat-first workspace and a right-side process panel that consumes the unified stream.

**Tech Stack:** Python 3.11+, FastAPI, sse-starlette, pytest, httpx ASGITransport, React 18, TypeScript, Vite, Vitest, Testing Library, lucide-react.

---

## File Structure

Create backend files:

- `backend/__init__.py`: package marker.
- `backend/main.py`: FastAPI app factory and router registration for the new Agent Gateway.
- `backend/models.py`: Pydantic request model and event type aliases.
- `backend/services/__init__.py`: service package marker.
- `backend/services/agent_router.py`: resolves requested mode into `rag` or `oncall`.
- `backend/services/agent_gateway.py`: streams unified events from existing RAG and OnCall services.
- `backend/api/__init__.py`: API package marker.
- `backend/api/agent.py`: `/api/agent/stream` SSE endpoint.

Create backend tests:

- `tests/test_backend_agent_router.py`: mode and auto route behavior.
- `tests/test_backend_agent_gateway_stream.py`: service-level event ordering and event conversion.
- `tests/test_backend_agent_api.py`: FastAPI endpoint contract and SSE body.

Create frontend files:

- `frontend/package.json`: Vite React scripts and dependencies.
- `frontend/index.html`: Vite entry HTML.
- `frontend/tsconfig.json`: TypeScript settings.
- `frontend/tsconfig.node.json`: Vite config TypeScript settings.
- `frontend/vite.config.ts`: Vite, dev proxy, and Vitest config.
- `frontend/src/main.tsx`: React bootstrap.
- `frontend/src/App.tsx`: app composition and run state.
- `frontend/src/styles.css`: application styling.
- `frontend/src/types/events.ts`: shared frontend event and run types.
- `frontend/src/api/agentStream.ts`: SSE-over-fetch stream client.
- `frontend/src/components/AppShell.tsx`: page shell.
- `frontend/src/components/Sidebar.tsx`: session list and new session action.
- `frontend/src/components/ChatWorkspace.tsx`: header, messages, and composer.
- `frontend/src/components/AgentProcessPanel.tsx`: route, timeline, events, evidence, report, and feedback.
- `frontend/src/components/__tests__/agentStream.test.ts`: stream parser tests.
- `frontend/src/components/__tests__/App.test.tsx`: core UI behavior tests.

Modify existing files:

- `pyproject.toml`: include `backend*` packages in setuptools discovery and coverage source.
- `.gitignore`: add `.superpowers/` and frontend build artifacts if absent.
- `README.md`: add new frontend/backend startup commands after implementation.

Implementation should not modify the existing `app/agent` core unless a failing test proves a bug in the existing public service contract.

---

### Task 1: Backend Router Contract

**Files:**
- Create: `backend/__init__.py`
- Create: `backend/models.py`
- Create: `backend/services/__init__.py`
- Create: `backend/services/agent_router.py`
- Test: `tests/test_backend_agent_router.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write the failing router tests**

Create `tests/test_backend_agent_router.py`:

```python
import pytest

from pydantic import ValidationError

from backend.models import AgentStreamRequest
from backend.services.agent_router import AgentRoute, AgentRouter


def test_explicit_rag_mode_routes_to_rag():
    router = AgentRouter()

    route = router.resolve_route(message="explain the runbook", mode="rag")

    assert route == AgentRoute(route="rag", reason="explicit_mode")


def test_explicit_oncall_mode_routes_to_oncall():
    router = AgentRouter()

    route = router.resolve_route(message="checkout-api is slow", mode="oncall")

    assert route == AgentRoute(route="oncall", reason="explicit_mode")


def test_auto_mode_uses_aiops_intent_for_incident_text():
    router = AgentRouter()

    route = router.resolve_route(message="CPU alert on checkout-api", mode="auto")

    assert route.route == "oncall"
    assert route.reason in {"matched_aiops_keyword", "llm_semantic_aiops"}


def test_auto_mode_defaults_to_rag_for_knowledge_text():
    router = AgentRouter()

    route = router.resolve_route(message="explain the deployment document", mode="auto")

    assert route.route == "rag"


def test_invalid_mode_is_rejected_by_pydantic():
    with pytest.raises(ValidationError):
        AgentStreamRequest(session_id="s1", message="hello", mode="bad-mode")
```

- [ ] **Step 2: Run the router test and verify it fails**

Run:

```powershell
python -m pytest tests/test_backend_agent_router.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backend'`.

- [ ] **Step 3: Add backend package discovery**

Modify `pyproject.toml`:

```toml
[tool.setuptools.packages.find]
where = ["."]
include = ["app*", "backend*"]
```

Also update coverage source:

```toml
[tool.coverage.run]
source = ["app", "backend"]
```

- [ ] **Step 4: Create backend model and router code**

Create `backend/__init__.py`:

```python
"""Frontend-facing Agent Gateway backend package."""
```

Create `backend/services/__init__.py`:

```python
"""Agent Gateway service layer."""
```

Create `backend/models.py`:

```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AgentMode = Literal["auto", "rag", "oncall"]
ResolvedAgentRoute = Literal["rag", "oncall"]


class AgentStreamRequest(BaseModel):
    session_id: str = Field(default="default", min_length=1)
    message: str = Field(min_length=1)
    mode: AgentMode = "auto"
```

Create `backend/services/agent_router.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from backend.models import AgentMode, ResolvedAgentRoute
from app.services.router_service import RouterService


@dataclass(frozen=True, slots=True)
class AgentRoute:
    route: ResolvedAgentRoute
    reason: str


class AgentRouter:
    """Resolve frontend-selected mode into an executable agent route."""

    def __init__(self, router_service: RouterService | None = None):
        self.router_service = router_service or RouterService()

    def resolve_route(self, *, message: str, mode: AgentMode) -> AgentRoute:
        if mode == "rag":
            return AgentRoute(route="rag", reason="explicit_mode")
        if mode == "oncall":
            return AgentRoute(route="oncall", reason="explicit_mode")

        decision = self.router_service.route_message(message)
        if decision.route == "aiops":
            return AgentRoute(route="oncall", reason=decision.reason)
        return AgentRoute(route="rag", reason=decision.reason)
```

- [ ] **Step 5: Run the router tests and verify they pass**

Run:

```powershell
python -m pytest tests/test_backend_agent_router.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit router contract**

Run:

```powershell
git add pyproject.toml backend/__init__.py backend/models.py backend/services/__init__.py backend/services/agent_router.py tests/test_backend_agent_router.py
git commit -m "feat: add agent gateway router"
```

---

### Task 2: Backend Gateway Stream Service

**Files:**
- Create: `backend/services/agent_gateway.py`
- Test: `tests/test_backend_agent_gateway_stream.py`

- [ ] **Step 1: Write failing gateway stream tests**

Create `tests/test_backend_agent_gateway_stream.py`:

```python
import pytest

from backend.services.agent_gateway import AgentGatewayService
from backend.services.agent_router import AgentRoute


class FakeRouter:
    def __init__(self, route):
        self.route = route

    def resolve_route(self, *, message, mode):
        return self.route


class FakeRagService:
    async def query_stream(self, message, session_id):
        yield {"type": "content", "data": "hello "}
        yield {"type": "content", "data": "world"}
        yield {"type": "complete", "data": {"answer": "hello world"}}


class FakeOnCallService:
    async def execute(self, message, session_id):
        yield {
            "type": "agent_event",
            "agent": "triage",
            "stage": "triage",
            "status": "completed",
            "summary": "Incident structured",
            "payload": {},
        }
        yield {
            "type": "tool_event",
            "agent": "evidence_collector",
            "tool": "query_metrics_alerts",
            "status": "completed",
            "evidence_id": "ev-1",
            "summary": "Collected metrics",
            "payload": {"duration_ms": 12},
        }
        yield {
            "type": "complete",
            "case_id": "case-1",
            "response": "# Report",
            "events": [],
        }


@pytest.mark.asyncio
async def test_rag_stream_starts_with_route_selected_and_finishes_complete():
    service = AgentGatewayService(
        router=FakeRouter(AgentRoute(route="rag", reason="explicit_mode")),
        rag_service=FakeRagService(),
        oncall_service=FakeOnCallService(),
    )

    events = [
        event
        async for event in service.stream(message="explain docs", session_id="s1", mode="rag")
    ]

    assert events[0] == {
        "type": "route_selected",
        "route": "rag",
        "reason": "explicit_mode",
        "mode": "rag",
    }
    assert events[1] == {"type": "content", "data": "hello "}
    assert events[2] == {"type": "content", "data": "world"}
    assert events[-1] == {
        "type": "complete",
        "route": "rag",
        "answer": "hello world",
        "case_id": "",
        "events": [],
    }


@pytest.mark.asyncio
async def test_oncall_stream_forwards_timeline_events_and_report():
    service = AgentGatewayService(
        router=FakeRouter(AgentRoute(route="oncall", reason="explicit_mode")),
        rag_service=FakeRagService(),
        oncall_service=FakeOnCallService(),
    )

    events = [
        event
        async for event in service.stream(message="checkout-api slow", session_id="s1", mode="oncall")
    ]

    assert events[0]["type"] == "route_selected"
    assert events[0]["route"] == "oncall"
    assert events[1]["type"] == "agent_event"
    assert events[2]["type"] == "tool_event"
    assert events[3] == {
        "type": "report",
        "route": "oncall",
        "case_id": "case-1",
        "report": "# Report",
    }
    assert events[4] == {
        "type": "complete",
        "route": "oncall",
        "answer": "# Report",
        "case_id": "case-1",
        "events": [],
    }
```

- [ ] **Step 2: Run the gateway tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_backend_agent_gateway_stream.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `backend.services.agent_gateway`.

- [ ] **Step 3: Implement the stream service**

Create `backend/services/agent_gateway.py`:

```python
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from backend.models import AgentMode
from backend.services.agent_router import AgentRouter
from app.services.aiops_service import aiops_service
from app.services.rag_agent_service import rag_agent_service

TIMELINE_EVENT_TYPES = {"agent_event", "tool_event", "decision_event"}


class AgentGatewayService:
    """Stream normalized frontend events from the selected agent service."""

    def __init__(
        self,
        router: AgentRouter | None = None,
        rag_service: Any | None = None,
        oncall_service: Any | None = None,
    ):
        self.router = router or AgentRouter()
        self.rag_service = rag_service or rag_agent_service
        self.oncall_service = oncall_service or aiops_service

    async def stream(
        self,
        *,
        message: str,
        session_id: str,
        mode: AgentMode,
    ) -> AsyncGenerator[dict[str, Any], None]:
        route = self.router.resolve_route(message=message, mode=mode)
        yield {
            "type": "route_selected",
            "route": route.route,
            "reason": route.reason,
            "mode": mode,
        }

        if route.route == "rag":
            async for event in self._stream_rag(message=message, session_id=session_id):
                yield event
            return

        async for event in self._stream_oncall(message=message, session_id=session_id):
            yield event

    async def _stream_rag(
        self,
        *,
        message: str,
        session_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        final_answer = ""
        async for chunk in self.rag_service.query_stream(message, session_id=session_id):
            chunk_type = chunk.get("type")
            if chunk_type == "content":
                data = str(chunk.get("data") or "")
                final_answer += data
                yield {"type": "content", "data": data}
            elif chunk_type == "complete":
                data = chunk.get("data")
                if isinstance(data, dict):
                    final_answer = str(data.get("answer") or final_answer)
                yield {
                    "type": "complete",
                    "route": "rag",
                    "answer": final_answer,
                    "case_id": "",
                    "events": [],
                }
            elif chunk_type == "error":
                yield {"type": "error", "route": "rag", "message": str(chunk.get("data") or "")}

    async def _stream_oncall(
        self,
        *,
        message: str,
        session_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        async for event in self.oncall_service.execute(message, session_id=session_id):
            event_type = event.get("type")
            if event_type in TIMELINE_EVENT_TYPES:
                yield dict(event)
            elif event_type == "report":
                yield {
                    "type": "report",
                    "route": "oncall",
                    "case_id": str(event.get("case_id") or ""),
                    "report": str(event.get("report") or ""),
                }
            elif event_type == "complete":
                case_id = str(event.get("case_id") or "")
                answer = str(event.get("response") or event.get("message") or "")
                events = event.get("events") if isinstance(event.get("events"), list) else []
                yield {
                    "type": "report",
                    "route": "oncall",
                    "case_id": case_id,
                    "report": answer,
                }
                yield {
                    "type": "complete",
                    "route": "oncall",
                    "answer": answer,
                    "case_id": case_id,
                    "events": events,
                }
            elif event_type == "error":
                yield {
                    "type": "error",
                    "route": "oncall",
                    "case_id": str(event.get("case_id") or ""),
                    "message": str(event.get("message") or "OnCall execution failed"),
                }


agent_gateway_service = AgentGatewayService()
```

- [ ] **Step 4: Run gateway tests and verify they pass**

Run:

```powershell
python -m pytest tests/test_backend_agent_gateway_stream.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit gateway stream service**

Run:

```powershell
git add backend/services/agent_gateway.py tests/test_backend_agent_gateway_stream.py
git commit -m "feat: stream agent gateway events"
```

---

### Task 3: Backend FastAPI SSE Endpoint

**Files:**
- Create: `backend/api/__init__.py`
- Create: `backend/api/agent.py`
- Create: `backend/main.py`
- Test: `tests/test_backend_agent_api.py`

- [ ] **Step 1: Write the failing API test**

Create `tests/test_backend_agent_api.py`:

```python
import json

import httpx
import pytest

from backend.api import agent as agent_api
from backend.main import app


class FakeGateway:
    async def stream(self, *, message, session_id, mode):
        yield {
            "type": "route_selected",
            "route": "oncall",
            "reason": "explicit_mode",
            "mode": mode,
        }
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
            "route": "oncall",
            "answer": "# Report",
            "case_id": "case-1",
            "events": [],
        }


@pytest.mark.asyncio
async def test_agent_stream_endpoint_returns_sse_events(monkeypatch):
    monkeypatch.setattr(agent_api, "agent_gateway_service", FakeGateway())
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/agent/stream",
            json={
                "session_id": "s1",
                "message": "checkout-api slow",
                "mode": "oncall",
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "route_selected" in response.text
    assert "agent_event" in response.text
    assert "case-1" in response.text


@pytest.mark.asyncio
async def test_agent_stream_endpoint_rejects_empty_message():
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/agent/stream",
            json={"session_id": "s1", "message": "", "mode": "auto"},
        )

    assert response.status_code == 422
```

- [ ] **Step 2: Run the API test and verify it fails**

Run:

```powershell
python -m pytest tests/test_backend_agent_api.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `backend.api`.

- [ ] **Step 3: Implement FastAPI endpoint**

Create `backend/api/__init__.py`:

```python
"""Agent Gateway API routes."""
```

Create `backend/api/agent.py`:

```python
from __future__ import annotations

import json

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from backend.models import AgentStreamRequest
from backend.services.agent_gateway import agent_gateway_service

router = APIRouter()


@router.post("/agent/stream")
async def stream_agent(request: AgentStreamRequest):
    async def event_generator():
        try:
            async for event in agent_gateway_service.stream(
                message=request.message,
                session_id=request.session_id,
                mode=request.mode,
            ):
                yield {
                    "event": "message",
                    "data": json.dumps(event, ensure_ascii=False),
                }
                if event.get("type") in {"complete", "error"}:
                    break
        except Exception as exc:
            yield {
                "event": "message",
                "data": json.dumps(
                    {
                        "type": "error",
                        "route": "unknown",
                        "message": str(exc),
                    },
                    ensure_ascii=False,
                ),
            }

    return EventSourceResponse(event_generator())
```

Create `backend/main.py`:

```python
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import agent

app = FastAPI(title="Agent Gateway", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent.router, prefix="/api", tags=["agent"])


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "agent-gateway"}
```

- [ ] **Step 4: Run API tests and verify they pass**

Run:

```powershell
python -m pytest tests/test_backend_agent_api.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit backend API**

Run:

```powershell
git add backend/api/__init__.py backend/api/agent.py backend/main.py tests/test_backend_agent_api.py
git commit -m "feat: expose agent gateway stream api"
```

---

### Task 4: Frontend Vite Scaffold and Types

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/index.html`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.node.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/types/events.ts`
- Modify: `.gitignore`

- [ ] **Step 1: Create frontend package and config**

Create `frontend/package.json`:

```json
{
  "name": "agent-gateway-ui",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite --host 0.0.0.0 --port 5173",
    "build": "tsc -b && vite build",
    "preview": "vite preview --host 0.0.0.0 --port 4173",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "lucide-react": "^0.468.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.6.3",
    "@testing-library/react": "^16.1.0",
    "@testing-library/user-event": "^14.5.2",
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.4",
    "jsdom": "^25.0.1",
    "typescript": "^5.6.3",
    "vite": "^6.0.1",
    "vitest": "^2.1.5"
  }
}
```

Create `frontend/index.html`:

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Agent Gateway</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

Create `frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["DOM", "DOM.Iterable", "ES2020"],
    "allowJs": false,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "forceConsistentCasingInFileNames": true,
    "module": "ESNext",
    "moduleResolution": "Node",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx"
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

Create `frontend/tsconfig.node.json`:

```json
{
  "compilerOptions": {
    "composite": true,
    "module": "ESNext",
    "moduleResolution": "Node",
    "allowSyntheticDefaultImports": true
  },
  "include": ["vite.config.ts"]
}
```

Create `frontend/vite.config.ts`:

```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: [],
  },
});
```

- [ ] **Step 2: Create frontend event types**

Create `frontend/src/types/events.ts`:

```ts
export type AgentMode = "auto" | "rag" | "oncall";
export type AgentRoute = "rag" | "oncall" | "unknown";
export type RunStatus = "idle" | "running" | "completed" | "error" | "cancelled";

export type RouteSelectedEvent = {
  type: "route_selected";
  route: AgentRoute;
  reason: string;
  mode: AgentMode;
};

export type TimelineEvent = {
  type: "agent_event" | "tool_event" | "decision_event";
  agent?: string;
  stage?: string;
  status?: string;
  summary?: string;
  tool?: string;
  evidence_id?: string;
  payload?: Record<string, unknown>;
};

export type ContentEvent = {
  type: "content";
  data: string;
};

export type ReportEvent = {
  type: "report";
  route: AgentRoute;
  case_id: string;
  report: string;
};

export type CompleteEvent = {
  type: "complete";
  route: AgentRoute;
  answer: string;
  case_id: string;
  events: TimelineEvent[];
};

export type ErrorEvent = {
  type: "error";
  route?: AgentRoute;
  case_id?: string;
  message: string;
};

export type AgentStreamEvent =
  | RouteSelectedEvent
  | TimelineEvent
  | ContentEvent
  | ReportEvent
  | CompleteEvent
  | ErrorEvent;

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  status?: RunStatus;
};

export type AgentRun = {
  runId: string;
  sessionId: string;
  mode: AgentMode;
  route: AgentRoute;
  status: RunStatus;
  events: TimelineEvent[];
  answer: string;
  caseId: string;
  error: string;
};
```

- [ ] **Step 3: Create React bootstrap**

Create `frontend/src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

- [ ] **Step 4: Update `.gitignore`**

Add these lines if they are absent:

```gitignore
.superpowers/
frontend/node_modules/
frontend/dist/
```

- [ ] **Step 5: Install dependencies and verify scaffold fails only because App is absent**

Run:

```powershell
cd frontend
npm install
npm run build
```

Expected: FAIL with `Cannot find module './App'`.

- [ ] **Step 6: Commit frontend scaffold**

Run:

```powershell
git add .gitignore frontend/package.json frontend/package-lock.json frontend/index.html frontend/tsconfig.json frontend/tsconfig.node.json frontend/vite.config.ts frontend/src/main.tsx frontend/src/types/events.ts
git commit -m "feat: scaffold react agent gateway ui"
```

---

### Task 5: Frontend Stream Client

**Files:**
- Create: `frontend/src/api/agentStream.ts`
- Test: `frontend/src/components/__tests__/agentStream.test.ts`

- [ ] **Step 1: Write failing stream client tests**

Create `frontend/src/components/__tests__/agentStream.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { parseSseChunk } from "../../api/agentStream";

describe("parseSseChunk", () => {
  it("parses multiple SSE message frames", () => {
    const events = parseSseChunk(
      'event: message\\ndata: {"type":"route_selected","route":"rag","reason":"explicit_mode","mode":"rag"}\\n\\n' +
        'event: message\\ndata: {"type":"content","data":"hello"}\\n\\n',
    );

    expect(events).toEqual([
      { type: "route_selected", route: "rag", reason: "explicit_mode", mode: "rag" },
      { type: "content", data: "hello" },
    ]);
  });

  it("ignores empty frames", () => {
    expect(parseSseChunk("\\n\\n")).toEqual([]);
  });
});
```

- [ ] **Step 2: Run the frontend test and verify it fails**

Run:

```powershell
cd frontend
npm run test -- src/components/__tests__/agentStream.test.ts
```

Expected: FAIL with missing `../../api/agentStream`.

- [ ] **Step 3: Implement stream parser and client**

Create `frontend/src/api/agentStream.ts`:

```ts
import type { AgentMode, AgentStreamEvent } from "../types/events";

export type StreamAgentArgs = {
  sessionId: string;
  message: string;
  mode: AgentMode;
  signal?: AbortSignal;
  onEvent: (event: AgentStreamEvent) => void;
};

export function parseSseChunk(chunk: string): AgentStreamEvent[] {
  return chunk
    .split("\\n\\n")
    .map((frame) => frame.trim())
    .filter(Boolean)
    .map((frame) => {
      const dataLine = frame
        .split("\\n")
        .find((line) => line.startsWith("data:"));
      if (!dataLine) {
        return null;
      }
      return JSON.parse(dataLine.slice("data:".length).trim()) as AgentStreamEvent;
    })
    .filter((event): event is AgentStreamEvent => event !== null);
}

export async function streamAgent(args: StreamAgentArgs): Promise<void> {
  const response = await fetch("/api/agent/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({
      session_id: args.sessionId,
      message: args.message,
      mode: args.mode,
    }),
    signal: args.signal,
  });

  if (!response.ok || !response.body) {
    throw new Error(`Agent stream failed with HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\\n\\n");
    buffer = frames.pop() ?? "";
    for (const event of parseSseChunk(frames.join("\\n\\n"))) {
      args.onEvent(event);
    }
  }

  for (const event of parseSseChunk(buffer)) {
    args.onEvent(event);
  }
}
```

- [ ] **Step 4: Run stream client tests**

Run:

```powershell
cd frontend
npm run test -- src/components/__tests__/agentStream.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit stream client**

Run:

```powershell
git add frontend/src/api/agentStream.ts frontend/src/components/__tests__/agentStream.test.ts
git commit -m "feat: add frontend agent stream client"
```

---

### Task 6: Frontend UI Components

**Files:**
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/components/AppShell.tsx`
- Create: `frontend/src/components/Sidebar.tsx`
- Create: `frontend/src/components/ChatWorkspace.tsx`
- Create: `frontend/src/components/AgentProcessPanel.tsx`
- Create: `frontend/src/styles.css`
- Test: `frontend/src/components/__tests__/App.test.tsx`

- [ ] **Step 1: Write failing UI tests**

Create `frontend/src/components/__tests__/App.test.tsx`:

```tsx
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import App from "../../App";

vi.mock("../../api/agentStream", () => ({
  streamAgent: vi.fn(async ({ onEvent }) => {
    onEvent({ type: "route_selected", route: "oncall", reason: "explicit_mode", mode: "oncall" });
    onEvent({
      type: "agent_event",
      agent: "triage",
      stage: "triage",
      status: "completed",
      summary: "Incident structured",
      payload: {},
    });
    onEvent({ type: "report", route: "oncall", case_id: "case-1", report: "# Report" });
    onEvent({ type: "complete", route: "oncall", answer: "# Report", case_id: "case-1", events: [] });
  }),
}));

describe("App", () => {
  it("sends a message and renders realtime agent events", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.selectOptions(screen.getByLabelText("Agent mode"), "oncall");
    await user.type(screen.getByLabelText("Message"), "checkout-api slow");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Incident structured")).toBeInTheDocument();
    expect(await screen.findByText("case-1")).toBeInTheDocument();
    expect(await screen.findByText("# Report")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the UI test and verify it fails**

Run:

```powershell
cd frontend
npm run test -- src/components/__tests__/App.test.tsx
```

Expected: FAIL with missing `../../App`.

- [ ] **Step 3: Implement app composition**

Create `frontend/src/App.tsx`:

```tsx
import { useMemo, useRef, useState } from "react";

import { streamAgent } from "./api/agentStream";
import { AgentProcessPanel } from "./components/AgentProcessPanel";
import { AppShell } from "./components/AppShell";
import { ChatWorkspace } from "./components/ChatWorkspace";
import { Sidebar } from "./components/Sidebar";
import type {
  AgentMode,
  AgentRun,
  AgentStreamEvent,
  ChatMessage,
  TimelineEvent,
} from "./types/events";

const initialRun: AgentRun = {
  runId: "",
  sessionId: "session-default",
  mode: "auto",
  route: "unknown",
  status: "idle",
  events: [],
  answer: "",
  caseId: "",
  error: "",
};

function createId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default function App() {
  const [mode, setMode] = useState<AgentMode>("auto");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [run, setRun] = useState<AgentRun>(initialRun);
  const abortRef = useRef<AbortController | null>(null);
  const sessionId = useMemo(() => createId("session"), []);

  function applyEvent(event: AgentStreamEvent) {
    setRun((current) => {
      if (event.type === "route_selected") {
        return { ...current, route: event.route, mode: event.mode, status: "running" };
      }
      if (event.type === "agent_event" || event.type === "tool_event" || event.type === "decision_event") {
        return { ...current, events: [...current.events, event as TimelineEvent] };
      }
      if (event.type === "content") {
        return { ...current, answer: `${current.answer}${event.data}` };
      }
      if (event.type === "report") {
        return { ...current, caseId: event.case_id, answer: event.report };
      }
      if (event.type === "complete") {
        setMessages((items) => [
          ...items.filter((item) => item.status !== "running"),
          { id: createId("assistant"), role: "assistant", content: event.answer, status: "completed" },
        ]);
        return {
          ...current,
          route: event.route,
          status: "completed",
          answer: event.answer,
          caseId: event.case_id,
          events: event.events.length > 0 ? event.events : current.events,
        };
      }
      return { ...current, status: "error", error: event.message };
    });
  }

  async function handleSend(message: string) {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const runId = createId("run");

    setMessages([
      ...messages,
      { id: createId("user"), role: "user", content: message },
      { id: createId("assistant"), role: "assistant", content: "Running...", status: "running" },
    ]);
    setRun({
      ...initialRun,
      runId,
      sessionId,
      mode,
      status: "running",
    });

    try {
      await streamAgent({
        sessionId,
        message,
        mode,
        signal: controller.signal,
        onEvent: applyEvent,
      });
    } catch (error) {
      if (!controller.signal.aborted) {
        setRun((current) => ({
          ...current,
          status: "error",
          error: error instanceof Error ? error.message : String(error),
        }));
      }
    }
  }

  function handleStop() {
    abortRef.current?.abort();
    setRun((current) => ({ ...current, status: "cancelled" }));
  }

  return (
    <AppShell
      sidebar={<Sidebar onNewSession={() => setMessages([])} />}
      main={
        <ChatWorkspace
          mode={mode}
          messages={messages}
          runStatus={run.status}
          onModeChange={setMode}
          onSend={handleSend}
          onStop={handleStop}
        />
      }
      panel={<AgentProcessPanel run={run} />}
    />
  );
}
```

- [ ] **Step 4: Implement shell and sidebar components**

Create `frontend/src/components/AppShell.tsx`:

```tsx
import type { ReactNode } from "react";

type AppShellProps = {
  sidebar: ReactNode;
  main: ReactNode;
  panel: ReactNode;
};

export function AppShell({ sidebar, main, panel }: AppShellProps) {
  return (
    <div className="app-shell">
      <aside className="sidebar">{sidebar}</aside>
      <main className="workspace">{main}</main>
      <aside className="process-panel">{panel}</aside>
    </div>
  );
}
```

Create `frontend/src/components/Sidebar.tsx`:

```tsx
import { MessageSquarePlus } from "lucide-react";

type SidebarProps = {
  onNewSession: () => void;
};

export function Sidebar({ onNewSession }: SidebarProps) {
  return (
    <div className="sidebar-inner">
      <h1>Agent Gateway</h1>
      <button className="sidebar-action" type="button" onClick={onNewSession}>
        <MessageSquarePlus size={18} />
        New session
      </button>
      <div className="sidebar-section">
        <span>Recent sessions</span>
        <p>No saved sessions yet</p>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Implement chat workspace**

Create `frontend/src/components/ChatWorkspace.tsx`:

```tsx
import { Send, Square } from "lucide-react";
import { FormEvent, useState } from "react";

import type { AgentMode, ChatMessage, RunStatus } from "../types/events";

type ChatWorkspaceProps = {
  mode: AgentMode;
  messages: ChatMessage[];
  runStatus: RunStatus;
  onModeChange: (mode: AgentMode) => void;
  onSend: (message: string) => void;
  onStop: () => void;
};

export function ChatWorkspace({
  mode,
  messages,
  runStatus,
  onModeChange,
  onSend,
  onStop,
}: ChatWorkspaceProps) {
  const [message, setMessage] = useState("");
  const isRunning = runStatus === "running";

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = message.trim();
    if (!trimmed || isRunning) {
      return;
    }
    setMessage("");
    onSend(trimmed);
  }

  return (
    <section className="chat-workspace">
      <header className="chat-header">
        <div>
          <h2>Operations Assistant</h2>
          <p>{isRunning ? "Streaming agent execution" : "Ready"}</p>
        </div>
        <label className="mode-select">
          <span>Agent mode</span>
          <select value={mode} onChange={(event) => onModeChange(event.target.value as AgentMode)}>
            <option value="auto">Auto</option>
            <option value="rag">Knowledge</option>
            <option value="oncall">OnCall</option>
          </select>
        </label>
      </header>

      <div className="messages">
        {messages.length === 0 ? (
          <div className="empty-state">Ask a question or start an OnCall diagnosis.</div>
        ) : (
          messages.map((item) => (
            <article className={`message ${item.role}`} key={item.id}>
              <div className="message-bubble">{item.content}</div>
            </article>
          ))
        )}
      </div>

      <form className="composer" onSubmit={submit}>
        <label className="sr-only" htmlFor="message-input">
          Message
        </label>
        <input
          id="message-input"
          aria-label="Message"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="Describe an incident or ask a question"
        />
        {isRunning ? (
          <button className="icon-button" type="button" onClick={onStop} aria-label="Stop">
            <Square size={18} />
          </button>
        ) : (
          <button className="icon-button primary" type="submit" aria-label="Send">
            <Send size={18} />
          </button>
        )}
      </form>
    </section>
  );
}
```

- [ ] **Step 6: Implement process panel**

Create `frontend/src/components/AgentProcessPanel.tsx`:

```tsx
import { Activity, CheckCircle2, CircleAlert, Wrench } from "lucide-react";

import type { AgentRun, TimelineEvent } from "../types/events";

type AgentProcessPanelProps = {
  run: AgentRun;
};

function eventIcon(event: TimelineEvent) {
  if (event.type === "tool_event") {
    return <Wrench size={16} />;
  }
  if (event.status === "completed") {
    return <CheckCircle2 size={16} />;
  }
  if (event.status === "failed" || event.status === "degraded") {
    return <CircleAlert size={16} />;
  }
  return <Activity size={16} />;
}

export function AgentProcessPanel({ run }: AgentProcessPanelProps) {
  return (
    <section className="agent-panel">
      <header>
        <h2>Agent process</h2>
        <span className={`status-pill ${run.status}`}>{run.status}</span>
      </header>

      <div className="panel-card">
        <span className="label">Route</span>
        <strong>{run.route}</strong>
        <p>Mode: {run.mode}</p>
      </div>

      <div className="panel-card">
        <span className="label">Timeline</span>
        {run.events.length === 0 ? (
          <p>No events yet</p>
        ) : (
          <ol className="timeline">
            {run.events.map((event, index) => (
              <li key={`${event.type}-${index}`}>
                <div className="timeline-icon">{eventIcon(event)}</div>
                <div>
                  <strong>{event.agent || event.tool || event.type}</strong>
                  <span>{event.stage || event.status || event.type}</span>
                  <p>{event.summary || "Event recorded"}</p>
                  {event.evidence_id ? <code>{event.evidence_id}</code> : null}
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>

      {run.caseId ? (
        <div className="panel-card">
          <span className="label">Case</span>
          <strong>{run.caseId}</strong>
        </div>
      ) : null}

      {run.answer ? (
        <div className="panel-card report-card">
          <span className="label">Report</span>
          <pre>{run.answer}</pre>
        </div>
      ) : null}

      {run.error ? (
        <div className="panel-card error-card">
          <span className="label">Error</span>
          <p>{run.error}</p>
        </div>
      ) : null}
    </section>
  );
}
```

- [ ] **Step 7: Add CSS**

Create `frontend/src/styles.css`:

```css
:root {
  color: #172033;
  background: #eef2f7;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-synthesis: none;
  text-rendering: optimizeLegibility;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-width: 320px;
  min-height: 100vh;
}

button,
input,
select {
  font: inherit;
}

.app-shell {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr) 380px;
  height: 100vh;
  overflow: hidden;
}

.sidebar {
  background: #101828;
  color: #f8fafc;
  border-right: 1px solid #1f2937;
}

.sidebar-inner {
  display: flex;
  flex-direction: column;
  gap: 18px;
  height: 100%;
  padding: 18px;
}

.sidebar h1,
.chat-header h2,
.agent-panel h2 {
  margin: 0;
  font-size: 18px;
  letter-spacing: 0;
}

.sidebar-action {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: 0;
  border-radius: 8px;
  color: #101828;
  background: #e0f2fe;
  padding: 10px 12px;
  cursor: pointer;
}

.sidebar-section {
  color: #cbd5e1;
  font-size: 13px;
}

.workspace {
  min-width: 0;
  background: #f8fafc;
}

.chat-workspace {
  display: grid;
  grid-template-rows: auto 1fr auto;
  height: 100%;
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 22px;
  border-bottom: 1px solid #d9e2ec;
  background: #ffffff;
}

.chat-header p {
  margin: 4px 0 0;
  color: #64748b;
  font-size: 13px;
}

.mode-select {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #475569;
  font-size: 13px;
}

.mode-select select {
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #ffffff;
  padding: 7px 10px;
}

.messages {
  overflow-y: auto;
  padding: 24px;
}

.empty-state {
  display: grid;
  place-items: center;
  min-height: 100%;
  color: #64748b;
}

.message {
  display: flex;
  margin-bottom: 14px;
}

.message.user {
  justify-content: flex-end;
}

.message-bubble {
  max-width: min(680px, 85%);
  border-radius: 8px;
  padding: 12px 14px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.message.user .message-bubble {
  background: #dbeafe;
}

.message.assistant .message-bubble {
  background: #ffffff;
  border: 1px solid #e2e8f0;
}

.composer {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 10px;
  padding: 18px 22px;
  background: #ffffff;
  border-top: 1px solid #d9e2ec;
}

.composer input {
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  min-height: 44px;
  padding: 0 14px;
}

.icon-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #ffffff;
  cursor: pointer;
}

.icon-button.primary {
  border-color: #2563eb;
  background: #2563eb;
  color: #ffffff;
}

.process-panel {
  min-width: 0;
  background: #ffffff;
  border-left: 1px solid #d9e2ec;
  overflow-y: auto;
}

.agent-panel {
  padding: 18px;
}

.agent-panel header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.status-pill {
  border-radius: 999px;
  padding: 4px 9px;
  background: #e2e8f0;
  color: #475569;
  font-size: 12px;
}

.status-pill.running {
  background: #dbeafe;
  color: #1d4ed8;
}

.status-pill.completed {
  background: #dcfce7;
  color: #15803d;
}

.status-pill.error {
  background: #fee2e2;
  color: #b91c1c;
}

.panel-card {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
  background: #ffffff;
}

.label {
  display: block;
  color: #64748b;
  font-size: 12px;
  margin-bottom: 6px;
}

.timeline {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.timeline li {
  display: grid;
  grid-template-columns: 28px 1fr;
  gap: 10px;
}

.timeline-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: #eff6ff;
  color: #2563eb;
}

.timeline span,
.timeline p {
  display: block;
  margin: 2px 0;
  color: #64748b;
  font-size: 13px;
}

.timeline code {
  font-size: 12px;
}

.report-card pre {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.error-card {
  border-color: #fecaca;
  background: #fef2f2;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@media (max-width: 1100px) {
  .app-shell {
    grid-template-columns: 220px minmax(0, 1fr);
  }

  .process-panel {
    grid-column: 1 / -1;
    height: 42vh;
    border-left: 0;
    border-top: 1px solid #d9e2ec;
  }
}

@media (max-width: 760px) {
  .app-shell {
    grid-template-columns: 1fr;
  }

  .sidebar {
    display: none;
  }

  .chat-header {
    align-items: flex-start;
    flex-direction: column;
  }
}
```

- [ ] **Step 8: Run frontend UI tests**

Run:

```powershell
cd frontend
npm run test -- src/components/__tests__/App.test.tsx
```

Expected: PASS.

- [ ] **Step 9: Run frontend build**

Run:

```powershell
cd frontend
npm run build
```

Expected: PASS.

- [ ] **Step 10: Commit UI components**

Run:

```powershell
git add frontend/src/App.tsx frontend/src/components/AppShell.tsx frontend/src/components/Sidebar.tsx frontend/src/components/ChatWorkspace.tsx frontend/src/components/AgentProcessPanel.tsx frontend/src/components/__tests__/App.test.tsx frontend/src/styles.css
git commit -m "feat: build realtime agent process ui"
```

---

### Task 7: Integration Verification and Docs

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add README commands**

Add this section to `README.md` after the existing startup instructions:

````markdown
### New Agent Gateway UI

Backend:

```powershell
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Frontend:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The frontend calls `POST /api/agent/stream` through the Vite
dev proxy and displays realtime agent events in the right-side process panel.
````

- [ ] **Step 2: Run backend focused tests**

Run:

```powershell
python -m pytest tests/test_backend_agent_router.py tests/test_backend_agent_gateway_stream.py tests/test_backend_agent_api.py -q
```

Expected: PASS.

- [ ] **Step 3: Run frontend tests and build**

Run:

```powershell
cd frontend
npm run test
npm run build
```

Expected: PASS for both commands.

- [ ] **Step 4: Run backend server**

Run:

```powershell
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Expected: server starts and `http://localhost:8000/api/health` returns:

```json
{"status":"ok","service":"agent-gateway"}
```

- [ ] **Step 5: Run frontend dev server**

Run:

```powershell
cd frontend
npm run dev
```

Expected: Vite starts on `http://localhost:5173`.

- [ ] **Step 6: Browser smoke test**

Open `http://localhost:5173` and verify:

- The page shows chat workspace and right-side agent process panel.
- Select `OnCall`.
- Send `checkout-api slow`.
- The process panel shows `route_selected`.
- If local LLM/MCP dependencies are unavailable, the UI still shows an error event without losing prior events.

- [ ] **Step 7: Commit docs and verification adjustments**

Run:

```powershell
git add README.md
git commit -m "docs: add agent gateway ui startup"
```

---

## Self-Review Checklist

- Spec coverage: the plan creates separate `frontend/` and `backend/`, reuses existing `app/`, supports `auto/rag/oncall`, streams realtime events, and implements the approved chat-plus-panel UI.
- Placeholder scan: no task contains `TBD`, `TODO`, or an undefined implementation handwave.
- Type consistency: backend mode names are `auto`, `rag`, `oncall`; resolved routes are `rag`, `oncall`; frontend event names match backend event names.
- Scope control: persistent run history, replay, event bus, and true backend cancellation remain outside this implementation plan.
