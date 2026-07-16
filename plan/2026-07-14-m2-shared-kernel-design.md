# M2 Shared Kernel Design Review (W6 document only)

> **Date**: 2026-07-14  
> **Status**: design only — **no production code in W6**  
> **Parent plan**: [2026-07-14-m2-w6-parallel-delegation-aux.md](./2026-07-14-m2-w6-parallel-delegation-aux.md)  
> **Roadmap**: WP-B2 (W7 implementation)

---

## 1. Problem

Today there are two tool-calling loops:

| Path | Module | Notes |
|---|---|---|
| Parent orchestrator | `app/agent/harness/loop.py` | plan / re-evidence / replan / verify / SSE |
| Expert subagent | `app/agent/experts/base.py` `ToolCallingExpert` | scoped tools + N model/tool rounds |

W5 already shares `GuardedToolExecutor` / `stream_tool_results` from `app/agent/agent_loop.py`, and caps delegated rounds (`HARNESS_DELEGATE_MAX_TOOL_ROUNDS`) + evidence-only return. Full shared kernel means **subagent = harness configuration**, not a second product loop.

---

## 2. Target shape (W7)

```text
delegate_to_expert / delegate_parallel
  → SubHarnessRunner.run(
        system=expert.system_prompt,
        tools=expert_tools,
        max_steps=delegate_max_tool_rounds,
        timeout=harness_delegate_timeout_seconds,
        close_after_tools=evidence_only,
        event_prefix=f"{parent_trace}:delegate:{route}",
    )
  → same GuardedToolExecutor + event shapes as parent
```

Suggested flag:

```text
HARNESS_SHARED_KERNEL_DELEGATION=true   # default true after golden set green
# false → legacy ToolCallingExpert.run
```

---

## 3. Interface sketch

```python
@dataclass
class SubHarnessConfig:
    agent_label: str
    system_prompt: str
    tools: list[RuntimeTool]
    max_steps: int
    timeout_seconds: float
    temperature: float = 0.3
    close_after_tools: bool = True
    allow_final_answer: bool = False  # parent synthesizes when True path off


async def run_sub_harness(
    config: SubHarnessConfig,
    *,
    message: str,
    session_id: str,
    trace_id: str,
    context: str = "",
) -> AsyncGenerator[dict, None]:
    ...
```

Experts become thin adapters: `get_tools()` + `system_prompt` only.

---

## 4. Risks

| Risk | Mitigation |
|---|---|
| Nested timeouts eat parent budget | Keep independent delegate timeout; parent outer timeout unchanged |
| Event shape drift breaks frontend | Reuse `make_agent_event` / `tool_event`; integration golden SSE fixtures |
| W5 evidence-only semantics regress | `close_after_tools` must be first-class on SubHarnessConfig |
| Parallel fan-out × shared kernel | Registry singletons must stay immutable; per-call config only |
| Log pipeline hook in log expert | Preserve `transform_tool_result` as optional postprocess on shared runner |

---

## 5. Migration plan (W7)

1. Implement `SubHarnessRunner` beside `loop.py` (no delete of `ToolCallingExpert`).
2. Wire `run_one_delegate` to shared path when flag true.
3. Golden set: metric/log/knowledge serial + parallel + aux probe.
4. AST/grep gate: delegated path does not call legacy 3-round loop when flag true (optional).
5. Default true only after minimal eval non-regression.

---

## 6. Explicit non-goals for W6

- No changes to `experts/base.py` main path beyond what W5 already did.
- No deletion of `ToolCallingExpert`.
- No frontend work for sub-harness visualization.

---

## 7. Decision for W7 Monday

Approve shared kernel default-on **or** freeze dual-loop with ADR if risk too high. Either way, document in L2 exit review.
