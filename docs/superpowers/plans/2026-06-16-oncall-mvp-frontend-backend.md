# OnCall MVP Frontend-Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the existing OnCall multi-agent backend to the static frontend so incident requests show a timeline, final report, case ID, and feedback flow.

**Architecture:** Keep FastAPI and the current static app. Stabilize the OnCall payloads from `/api/assistant` and `/api/aiops`, then add focused frontend helpers that render structured timeline events and feedback controls inside existing assistant messages.

**Tech Stack:** FastAPI, pytest, SSE, vanilla JavaScript, CSS, existing OnCall coordinator services.

---

## File Structure

- Modify `app/services/router_service.py`: keep collecting normalized OnCall events, add a small helper for deduplication if needed.
- Modify `app/services/aiops_service.py`: preserve `events` in `diagnose()` completion payload for streaming clients.
- Modify `app/api/aiops.py`: keep thin SSE pass-through and make tests deterministic.
- Modify `static/app.js`: add timeline rendering, report rendering, feedback submission, and assistant response integration.
- Modify `static/styles.css`: add compact timeline/report/feedback styling in the current visual system.
- Add `tests/test_oncall_mvp_backend_contract.py`: focused backend contract tests for assistant and streaming payloads.

## Task 1: Backend Contract Coverage

**Files:**
- Create: `tests/test_oncall_mvp_backend_contract.py`
- Modify: `app/services/aiops_service.py`

- [ ] **Step 1: Write failing backend contract tests**

Create `tests/test_oncall_mvp_backend_contract.py`:

```python
import json

import pytest

from app.api import aiops as aiops_api
from app.services.aiops_service import AIOpsService
from app.services.router_service import RouteDecision, RouterService


class _FakeAIOpsForAssistant:
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
            "type": "complete",
            "case_id": "case-mvp",
            "response": "# Report",
            "events": [
                {
                    "type": "agent_event",
                    "agent": "report",
                    "stage": "report",
                    "status": "completed",
                    "summary": "Report generated",
                    "payload": {},
                }
            ],
        }


class _FakeRag:
    async def query(self, message, session_id):
        return "rag answer"


class _FakeAIOpsForApi:
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
            "diagnosis": {"status": "completed", "case_id": "case-mvp", "report": "# Report"},
            "events": [
                {
                    "type": "agent_event",
                    "agent": "triage",
                    "stage": "triage",
                    "status": "completed",
                    "summary": "Incident structured",
                    "payload": {},
                }
            ],
        }


class _FakeMemory:
    def create_case(self, session_id, user_input):
        return "case-mvp"

    def update_case_plan(self, case_id, plan):
        pass

    def complete_case(self, case_id, executed_steps, final_report):
        self.completed = {
            "case_id": case_id,
            "executed_steps": executed_steps,
            "final_report": final_report,
        }

    def fail_case(self, case_id, error_message):
        self.failed = {"case_id": case_id, "error_message": error_message}


@pytest.mark.asyncio
async def test_assistant_aiops_payload_contains_case_report_and_events(monkeypatch):
    service = RouterService(
        semantic_router=lambda message: RouteDecision(route="aiops", reason="test_aiops")
    )
    monkeypatch.setattr("app.services.router_service.aiops_service", _FakeAIOpsForAssistant())
    monkeypatch.setattr("app.services.router_service.rag_agent_service", _FakeRag())

    response = await service.answer("checkout-api slow", session_id="s1")

    assert response["success"] is True
    assert response["route"] == "aiops"
    assert response["route_reason"] == "test_aiops"
    assert response["case_id"] == "case-mvp"
    assert response["answer"] == "# Report"
    assert response["events"] == [
        {
            "type": "agent_event",
            "agent": "triage",
            "stage": "triage",
            "status": "completed",
            "summary": "Incident structured",
            "payload": {},
        },
        {
            "type": "agent_event",
            "agent": "report",
            "stage": "report",
            "status": "completed",
            "summary": "Report generated",
            "payload": {},
        },
    ]


@pytest.mark.asyncio
async def test_aiops_api_sse_complete_event_preserves_events(monkeypatch, api_client):
    monkeypatch.setattr(aiops_api, "aiops_service", _FakeAIOpsForApi(), raising=False)

    response = await api_client.post(
        "/api/aiops",
        headers={"X-Session-Owner": "owner-a"},
        json={"session_id": "s1"},
    )

    assert response.status_code == 200
    body = response.text
    assert "agent_event" in body
    assert "case-mvp" in body
    assert "Incident structured" in body


def test_diagnose_completion_includes_events_from_execute():
    async def fake_execute(user_input, session_id):
        yield {
            "type": "complete",
            "case_id": "case-mvp",
            "response": "# Report",
            "events": [{"type": "agent_event", "agent": "report", "summary": "done"}],
        }

    service = AIOpsService(memory_service=_FakeMemory(), checkpointer=None)
    service.execute = fake_execute

    async def collect():
        return [event async for event in service.diagnose(session_id="s1")]

    import asyncio

    events = asyncio.run(collect())
    complete = events[-1]

    assert complete["type"] == "complete"
    assert complete["diagnosis"]["case_id"] == "case-mvp"
    assert complete["diagnosis"]["report"] == "# Report"
    assert complete["events"] == [{"type": "agent_event", "agent": "report", "summary": "done"}]
```

- [ ] **Step 2: Run the backend tests to verify failure**

Run:

```powershell
python -m pytest tests/test_oncall_mvp_backend_contract.py -q
```

Expected: at least `test_diagnose_completion_includes_events_from_execute` fails because `diagnose()` drops the `events` field from complete events.

- [ ] **Step 3: Preserve events in `AIOpsService.diagnose()` completion**

In `app/services/aiops_service.py`, inside `diagnose()`, update the `complete` conversion to include `events`:

```python
yield {
    "type": "complete",
    "stage": "diagnosis_complete",
    "message": "璇婃柇娴佺▼瀹屾垚",
    "diagnosis": {
        "status": "completed",
        "case_id": event.get("case_id", ""),
        "report": event.get("response", ""),
    },
    "events": event.get("events", []),
}
```

- [ ] **Step 4: Run the backend tests to verify pass**

Run:

```powershell
python -m pytest tests/test_oncall_mvp_backend_contract.py -q
```

Expected: PASS.

## Task 2: Frontend Timeline Rendering Unit Surface

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Add browser-console test hooks manually before production changes**

Open the app after implementation and verify these functions exist on the app instance:

```javascript
typeof window.aiOpsApp.normalizeOnCallEvent === 'function'
typeof window.aiOpsApp.renderOnCallTimeline === 'function'
typeof window.aiOpsApp.renderOnCallReport === 'function'
```

Expected before implementation: these expressions fail because `window.aiOpsApp` or the methods do not exist.

- [ ] **Step 2: Expose the app instance for smoke testing**

At the bottom of `static/app.js`, replace the current initialization:

```javascript
document.addEventListener('DOMContentLoaded', () => {
    new AIOpsAssistantApp();
});
```

with:

```javascript
document.addEventListener('DOMContentLoaded', () => {
    window.aiOpsApp = new AIOpsAssistantApp();
});
```

- [ ] **Step 3: Add timeline helper methods to `AIOpsAssistantApp`**

Add these methods before `escapeHtml(text)`:

```javascript
    normalizeOnCallEvent(event) {
        if (!event || typeof event !== 'object') {
            return null;
        }

        const type = event.type || 'status';
        const agent = event.agent || event.tool || event.stage || 'system';
        const status = event.status || event.stage || '';
        const summary = event.summary || event.message || event.current_step || '';
        const payload = event.payload || event.diagnosis || event.plan || event;

        return { type, agent, status, summary, payload };
    }

    createOnCallTimeline(events) {
        const normalizedEvents = (events || [])
            .map(event => this.normalizeOnCallEvent(event))
            .filter(Boolean);

        const timeline = document.createElement('div');
        timeline.className = 'oncall-timeline';

        const title = document.createElement('div');
        title.className = 'oncall-section-title';
        title.textContent = '诊断时间线';
        timeline.appendChild(title);

        if (normalizedEvents.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'oncall-empty';
            empty.textContent = '暂无诊断事件';
            timeline.appendChild(empty);
            return timeline;
        }

        normalizedEvents.forEach((event, index) => {
            const item = document.createElement('div');
            item.className = `oncall-timeline-item ${this.escapeHtml(event.type)}`;

            const marker = document.createElement('div');
            marker.className = 'oncall-timeline-marker';
            marker.textContent = String(index + 1);

            const body = document.createElement('div');
            body.className = 'oncall-timeline-body';

            const meta = document.createElement('div');
            meta.className = 'oncall-timeline-meta';
            meta.textContent = [event.type, event.agent, event.status].filter(Boolean).join(' · ');

            const summary = document.createElement('div');
            summary.className = 'oncall-timeline-summary';
            summary.textContent = event.summary || '事件已记录';

            body.appendChild(meta);
            body.appendChild(summary);
            item.appendChild(marker);
            item.appendChild(body);
            timeline.appendChild(item);
        });

        return timeline;
    }

    renderOnCallTimeline(messageElement, events) {
        if (!messageElement) return;
        const wrapper = messageElement.querySelector('.message-content-wrapper');
        if (!wrapper) return;

        const existing = wrapper.querySelector('.oncall-timeline');
        if (existing) {
            existing.remove();
        }

        wrapper.insertBefore(this.createOnCallTimeline(events), wrapper.firstChild);
    }

    renderOnCallReport(messageElement, report, caseId) {
        if (!messageElement) return;
        const content = messageElement.querySelector('.message-content');
        if (!content) return;

        const header = caseId ? `诊断 Case：${caseId}\n\n` : '';
        content.innerHTML = this.renderMarkdown(`${header}${report || '暂无诊断报告'}`);
        this.highlightCodeBlocks(content);
    }
```

- [ ] **Step 4: Run a syntax check**

Run:

```powershell
node --check static/app.js
```

Expected: PASS with no syntax errors.

## Task 3: Frontend Assistant OnCall Integration

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Identify the quick assistant response branch**

Find the `sendQuickMessage` method and the branch that handles `data.data.route === 'aiops'`.

Run:

```powershell
rg -n "sendQuickMessage|route|events|case_id|answer" static\app.js
```

Expected: locate the code that currently calls `this.addMessage('assistant', answer)`.

- [ ] **Step 2: Add helper to render assistant OnCall responses**

Add this method before `renderOnCallTimeline(messageElement, events)`:

```javascript
    addOnCallAssistantMessage({ answer, events, caseId }) {
        const messageElement = this.addMessage('assistant', '', false, false);
        messageElement.classList.add('aiops-message', 'oncall-result-message');
        this.renderOnCallTimeline(messageElement, events || []);
        this.renderOnCallReport(messageElement, answer || '', caseId || '');
        this.renderOnCallFeedback(messageElement, caseId || '');

        this.currentChatHistory.push({
            type: 'assistant',
            content: answer || '',
            timestamp: new Date().toISOString(),
            metadata: {
                case_id: caseId || '',
                events: events || [],
                route: 'aiops',
            },
        });

        this.scrollToBottom();
        return messageElement;
    }
```

- [ ] **Step 3: Update quick assistant response handling**

In `sendQuickMessage`, replace the OnCall route rendering branch with:

```javascript
                if (responseData.route === 'aiops') {
                    this.addOnCallAssistantMessage({
                        answer,
                        events: responseData.events || [],
                        caseId: responseData.case_id || '',
                    });
                } else {
                    this.addMessage('assistant', answer);
                }
```

Keep the existing fallback/error handling around it.

- [ ] **Step 4: Run syntax check**

Run:

```powershell
node --check static/app.js
```

Expected: PASS.

## Task 4: Streaming Timeline Integration

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Add streaming state fields**

Inside `sendAIOpsRequest(loadingMessageElement)`, after `let fullResponse = '';`, add:

```javascript
            const timelineEvents = [];
            let activeCaseId = '';
```

- [ ] **Step 2: Add local event handler in `sendAIOpsRequest`**

Inside `sendAIOpsRequest`, before the `while (true)` loop, add:

```javascript
            const applyAIOpsEvent = (sseMessage) => {
                if (!sseMessage || !sseMessage.type) {
                    return false;
                }

                if (['agent_event', 'tool_event', 'decision_event'].includes(sseMessage.type)) {
                    timelineEvents.push(sseMessage);
                    this.renderOnCallTimeline(loadingMessageElement, timelineEvents);
                    return true;
                }

                if (sseMessage.type === 'complete') {
                    activeCaseId = sseMessage.case_id || (sseMessage.diagnosis && sseMessage.diagnosis.case_id) || activeCaseId;
                    const report = sseMessage.response || (sseMessage.diagnosis && sseMessage.diagnosis.report) || fullResponse;
                    const finalEvents = sseMessage.events || timelineEvents;
                    this.updateAIOpsMessage(loadingMessageElement, report, []);
                    this.renderOnCallTimeline(loadingMessageElement, finalEvents);
                    this.renderOnCallReport(loadingMessageElement, report, activeCaseId);
                    this.renderOnCallFeedback(loadingMessageElement, activeCaseId);
                    return true;
                }

                if (sseMessage.type === 'report') {
                    const report = sseMessage.report || '';
                    fullResponse += `\n\n${report}`;
                    this.updateAIOpsStreamContent(loadingMessageElement, fullResponse);
                    return true;
                }

                return false;
            };
```

- [ ] **Step 3: Call the handler after JSON parse**

In each place where `const sseMessage = JSON.parse(...)` is followed by `if (sseMessage && sseMessage.type)`, add this guard first:

```javascript
                                        if (applyAIOpsEvent(sseMessage)) {
                                            return false;
                                        }
```

For the single-message parse branch, use:

```javascript
                                        if (applyAIOpsEvent(sseMessage)) {
                                            return;
                                        }
```

- [ ] **Step 4: Run syntax check**

Run:

```powershell
node --check static/app.js
```

Expected: PASS.

## Task 5: Feedback UI

**Files:**
- Modify: `static/app.js`
- Modify: `static/styles.css`

- [ ] **Step 1: Add feedback rendering method**

Add this method before `escapeHtml(text)`:

```javascript
    renderOnCallFeedback(messageElement, caseId) {
        if (!messageElement || !caseId) return;
        const wrapper = messageElement.querySelector('.message-content-wrapper');
        if (!wrapper || wrapper.querySelector('.oncall-feedback')) return;

        const feedback = document.createElement('form');
        feedback.className = 'oncall-feedback';
        feedback.innerHTML = `
            <div class="oncall-section-title">诊断反馈</div>
            <div class="oncall-feedback-row">
                <label><input type="radio" name="accepted" value="true" checked> 结论准确</label>
                <label><input type="radio" name="accepted" value="false"> 需要修正</label>
            </div>
            <input class="oncall-feedback-input" name="actual_root_cause" placeholder="实际根因">
            <input class="oncall-feedback-input" name="final_resolution" placeholder="最终处理方案">
            <textarea class="oncall-feedback-input" name="comment" rows="2" placeholder="补充说明"></textarea>
            <button type="submit" class="oncall-feedback-submit">提交反馈</button>
            <div class="oncall-feedback-status" aria-live="polite"></div>
        `;

        feedback.addEventListener('submit', async (event) => {
            event.preventDefault();
            await this.submitOnCallFeedback(feedback, caseId);
        });

        wrapper.appendChild(feedback);
    }
```

- [ ] **Step 2: Add feedback submit method**

Add this method after `renderOnCallFeedback(messageElement, caseId)`:

```javascript
    async submitOnCallFeedback(form, caseId) {
        const status = form.querySelector('.oncall-feedback-status');
        const submitButton = form.querySelector('.oncall-feedback-submit');
        const formData = new FormData(form);

        const payload = {
            case_id: caseId,
            session_id: this.sessionId,
            user_accepted: formData.get('accepted') === 'true',
            actual_root_cause: formData.get('actual_root_cause') || '',
            final_resolution: formData.get('final_resolution') || '',
            comment: formData.get('comment') || '',
        };

        if (status) status.textContent = '正在提交...';
        if (submitButton) submitButton.disabled = true;

        try {
            const response = await fetch(`${this.apiBaseUrl}/aiops/feedback`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...this.sessionHeaders(),
                },
                body: JSON.stringify(payload),
            });
            const data = await response.json();
            if (!response.ok || data.code !== 200) {
                throw new Error(data.message || '反馈提交失败');
            }
            if (status) status.textContent = '反馈已提交';
        } catch (error) {
            if (status) status.textContent = `提交失败：${error.message}`;
        } finally {
            if (submitButton) submitButton.disabled = false;
        }
    }
```

- [ ] **Step 3: Add styles**

Append to `static/styles.css`:

```css
.oncall-result-message .message-content-wrapper {
    width: min(860px, 100%);
}

.oncall-section-title {
    font-size: 13px;
    font-weight: 700;
    color: #263238;
    margin: 0 0 10px;
}

.oncall-timeline {
    border: 1px solid #dde5ec;
    border-radius: 8px;
    padding: 12px;
    margin-bottom: 12px;
    background: #f8fafc;
}

.oncall-timeline-item {
    display: grid;
    grid-template-columns: 28px 1fr;
    gap: 10px;
    padding: 8px 0;
}

.oncall-timeline-item + .oncall-timeline-item {
    border-top: 1px solid #e6edf3;
}

.oncall-timeline-marker {
    width: 24px;
    height: 24px;
    border-radius: 50%;
    background: #2563eb;
    color: #fff;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    font-weight: 700;
}

.oncall-timeline-meta {
    font-size: 12px;
    color: #607080;
    margin-bottom: 2px;
}

.oncall-timeline-summary {
    font-size: 14px;
    color: #1f2933;
    line-height: 1.5;
}

.oncall-empty {
    font-size: 13px;
    color: #718096;
}

.oncall-feedback {
    border: 1px solid #dde5ec;
    border-radius: 8px;
    padding: 12px;
    margin-top: 12px;
    background: #ffffff;
}

.oncall-feedback-row {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
    font-size: 13px;
    margin-bottom: 10px;
}

.oncall-feedback-input {
    width: 100%;
    box-sizing: border-box;
    border: 1px solid #cfd8e3;
    border-radius: 6px;
    padding: 8px 10px;
    margin-bottom: 8px;
    font: inherit;
    resize: vertical;
}

.oncall-feedback-submit {
    border: 0;
    border-radius: 6px;
    background: #2563eb;
    color: #fff;
    padding: 8px 14px;
    font-weight: 700;
    cursor: pointer;
}

.oncall-feedback-submit:disabled {
    opacity: 0.55;
    cursor: not-allowed;
}

.oncall-feedback-status {
    display: inline-block;
    margin-left: 10px;
    font-size: 13px;
    color: #52616f;
}
```

- [ ] **Step 4: Run syntax check**

Run:

```powershell
node --check static/app.js
```

Expected: PASS.

## Task 6: Verification And Smoke Test

**Files:**
- No production edits unless verification finds a real issue.

- [ ] **Step 1: Run targeted backend tests**

Run:

```powershell
python -m pytest tests/test_oncall_mvp_backend_contract.py tests/test_assistant_oncall_events.py tests/test_aiops_stream_events.py tests/test_aiops_feedback_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend syntax check**

Run:

```powershell
node --check static/app.js
```

Expected: PASS.

- [ ] **Step 3: Start local server**

Run:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 9900
```

Expected: server starts. If Milvus or external services are unavailable, record the exact startup error and continue with test evidence only.

- [ ] **Step 4: Browser smoke check**

Open:

```text
http://127.0.0.1:9900
```

Verify:

- the page loads,
- `window.aiOpsApp` exists,
- the AI Ops button still starts a request,
- no JavaScript syntax error appears in the console.

- [ ] **Step 5: Capture git status**

Run:

```powershell
git status --short
```

Expected: only intentional modified files and pre-existing unrelated changes remain.

## Self-Review

Spec coverage:

- Backend assistant payload is covered by Task 1.
- Streaming event compatibility is covered by Task 1 and Task 4.
- Timeline, report, and feedback UI are covered by Tasks 2 through 5.
- Existing chat/upload preservation is handled by scoped frontend changes and Task 6 smoke checks.

Placeholder scan:

- The plan has no TBD/TODO placeholders.

Type consistency:

- Frontend helpers consistently use `caseId`, `events`, and `answer`.
- Backend tests consistently expect `case_id`, `answer`, `events`, and `diagnosis.report`.

