import { describe, expect, it } from "vitest";

import type { AgentRun, TimelineEvent } from "../../../types/events";
import { buildInlineActivityModel } from "../inlineActivityModel";

function run(events: TimelineEvent[], status: AgentRun["status"] = "running"): AgentRun {
  return {
    runId: "assistant-1",
    sessionId: "session-1",
    mode: "auto",
    route: "diagnosis",
    status,
    events,
    answer: status === "completed" ? "done" : "",
    caseId: "",
    error: "",
    userMessage: "check cpu",
    feedback: "",
  };
}

describe("buildInlineActivityModel", () => {
  it("orders route, plan, tool start, and tool completion", () => {
    const model = buildInlineActivityModel(
      run([
        { type: "route_event", route: "diagnosis", status: "completed" },
        { type: "agent_event", stage: "plan", status: "completed" },
        {
          type: "agent_event",
          stage: "tool_start",
          status: "in_progress",
          payload: { tool: "query_prometheus_alerts", tool_call_id: "activity-1" },
        },
        {
          type: "tool_event",
          tool: "query_prometheus_alerts",
          status: "completed",
          duration_ms: 125,
          payload: { tool_call_id: "activity-1" },
        },
      ]),
    );

    expect(model.items.map((item) => item.kind)).toEqual(["route", "plan", "tool"]);
    expect(model.feed.map((item) => item.id)).toEqual([
      "route",
      "plan",
      "tool:activity-1",
    ]);
    expect(model.items[2]).toMatchObject({ state: "completed", durationMs: 125 });
    expect(model.feed[2]).toMatchObject({
      state: "completed",
      description: "读取当前告警状态，补充排查所需的只读信息。",
      details: expect.arrayContaining([
        { label: "节点类型", value: "工具调用" },
        { label: "执行状态", value: "已完成" },
        { label: "耗时", value: "125 毫秒" },
      ]),
    });
    expect(model.toolCount).toBe(1);
  });

  it.each([2, 3, 8])("keeps %i parallel experts as vertical child rows", (count) => {
    const experts = Array.from({ length: count }, (_, index) =>
      ["metric", "log", "change", "knowledge", "metric_secondary", "log_secondary", "change_secondary", "knowledge_secondary"][index],
    );
    const model = buildInlineActivityModel(
      run([
        {
          type: "agent_event",
          stage: "delegate_parallel_start",
          status: "in_progress",
          payload: { experts, tool_call_id: "activity-8", parallel: true },
        },
      ]),
    );

    expect(model.items).toHaveLength(1);
    expect(model.items[0].kind).toBe("expert-group");
    expect(model.items[0].children).toHaveLength(count);
    expect(model.items[0].children?.every((item) => item.kind === "expert")).toBe(true);
    expect(model.feed).toHaveLength(count + 1);
    expect(model.feed.slice(1).every((item) => item.kind === "expert")).toBe(true);
    expect(model.feed[0].details).toContainEqual({ label: "Agent 数量", value: `${count} 位` });
    expect(model.expertCount).toBe(count);
  });

  it("attributes child events and merges duplicate terminal events by opaque id", () => {
    const terminal: TimelineEvent = {
      type: "tool_event",
      tool: "search_app_logs",
      status: "completed",
      payload: { tool_call_id: "activity-2" },
    };
    const model = buildInlineActivityModel(
      run([
        {
          type: "agent_event",
          stage: "delegate_start",
          status: "in_progress",
          payload: { delegated_expert: "log", tool_call_id: "activity-1" },
        },
        {
          type: "agent_event",
          agent: "log_expert",
          stage: "complete",
          status: "completed",
          payload: { delegated_expert: "log", parent_tool_call_id: "activity-1" },
        },
        {
          type: "tool_event",
          agent: "log_expert",
          tool: "search_app_logs",
          status: "completed",
          payload: {
            delegated_expert: "log",
            parent_tool_call_id: "activity-1",
            tool_call_id: "activity-child-tool",
          },
        },
        {
          type: "agent_event",
          stage: "tool_start",
          status: "in_progress",
          payload: { tool: "search_app_logs", tool_call_id: "activity-2" },
        },
        terminal,
        terminal,
      ]),
    );

    expect(model.items[0].children?.[0].state).toBe("completed");
    expect(model.items[0].children?.[0].children?.[0]).toMatchObject({
      kind: "tool",
      label: "检索应用日志",
      state: "completed",
    });
    expect(model.items.filter((item) => item.kind === "tool")).toHaveLength(1);
    expect(model.feed.map((item) => item.id)).toEqual([
      "experts:activity-1",
      "experts:activity-1:log",
      "tool:activity-child-tool",
      "tool:activity-2",
    ]);
    expect(model.feed[2]).toMatchObject({
      parentId: "experts:activity-1:log",
      depth: 2,
      details: expect.arrayContaining([{ label: "所属 Agent", value: "日志专家" }]),
    });
    expect(model.toolCount).toBe(2);
  });

  it("keeps parented child dispatch and MCP tool lifecycle beneath the owning expert", () => {
    const model = buildInlineActivityModel(
      run([
        {
          type: "agent_event",
          stage: "delegate_parallel_start",
          status: "in_progress",
          payload: {
            experts: ["metric", "log"],
            tool_call_id: "activity-parent",
            parallel: true,
          },
        },
        {
          type: "agent_event",
          agent: "metric_expert",
          stage: "delegate_start",
          status: "in_progress",
          payload: {
            delegated_expert: "metric",
            parent_tool_call_id: "activity-parent",
            tool_call_id: "activity-child-dispatch",
          },
        },
        {
          type: "agent_event",
          agent: "metric_expert",
          stage: "tool_start",
          status: "in_progress",
          payload: {
            delegated_expert: "metric",
            parent_tool_call_id: "activity-parent",
            tool: "query_memory_metrics",
            tool_call_id: "activity-metric-tool",
          },
        },
        {
          type: "tool_event",
          agent: "metric_expert",
          tool: "query_memory_metrics",
          status: "completed",
          duration_ms: 240,
          payload: {
            delegated_expert: "metric",
            parent_tool_call_id: "activity-parent",
            tool_call_id: "activity-metric-tool",
          },
        },
        {
          type: "agent_event",
          agent: "log_expert",
          stage: "tool_start",
          status: "in_progress",
          payload: {
            delegated_expert: "log",
            parent_tool_call_id: "activity-parent",
            tool: "search_app_logs",
            tool_call_id: "activity-log-tool",
          },
        },
        {
          type: "tool_event",
          agent: "log_expert",
          tool: "search_app_logs",
          status: "completed",
          payload: {
            delegated_expert: "log",
            parent_tool_call_id: "activity-parent",
            tool_call_id: "activity-log-tool",
          },
        },
      ]),
    );

    expect(model.items.filter((item) => item.kind === "expert-group")).toHaveLength(1);
    expect(model.feed.map((item) => item.id)).toEqual([
      "experts:activity-parent",
      "experts:activity-parent:metric",
      "experts:activity-parent:log",
      "tool:activity-metric-tool",
      "tool:activity-log-tool",
    ]);
    expect(model.feed.find((item) => item.id === "tool:activity-metric-tool")).toMatchObject({
      parentId: "experts:activity-parent:metric",
      depth: 2,
      state: "completed",
      durationMs: 240,
    });
    expect(model.feed.find((item) => item.id === "tool:activity-log-tool")).toMatchObject({
      parentId: "experts:activity-parent:log",
      depth: 2,
      state: "completed",
    });
  });

  it("never exposes unknown function names or private payload data", () => {
    const model = buildInlineActivityModel(
      run([
        {
          type: "agent_event",
          stage: "tool_start",
          status: "in_progress",
          summary: "SECRET_PROMPT",
          trace_id: "SECRET_TRACE",
          payload: {
            tool: "internal_customer_dump",
            tool_call_id: "activity-1",
            arguments: { tenant: "SECRET_ARGUMENT" },
            result: "SECRET_RESULT",
          },
        },
      ]),
    );
    const serialized = JSON.stringify(model);

    expect(model.items[0].label).toBe("执行检查");
    expect(serialized).not.toContain("internal_customer_dump");
    expect(serialized).not.toContain("SECRET_PROMPT");
    expect(serialized).not.toContain("SECRET_TRACE");
    expect(serialized).not.toContain("SECRET_ARGUMENT");
    expect(serialized).not.toContain("SECRET_RESULT");
    expect(model.feed[0].details).toEqual([
      { label: "节点类型", value: "工具调用" },
      { label: "执行状态", value: "进行中" },
    ]);
  });

  it("summarizes unique tools and experts after completion", () => {
    const model = buildInlineActivityModel(
      run(
        [
          { type: "route_event", route: "diagnosis", status: "completed" },
          {
            type: "agent_event",
            stage: "delegate_parallel_start",
            status: "in_progress",
            payload: {
              experts: ["metric", "log", "change"],
              tool_call_id: "activity-1",
            },
          },
          {
            type: "agent_event",
            stage: "delegate_parallel_done",
            status: "completed",
            duration_ms: 800,
            payload: {
              tool_call_id: "activity-1",
              results: [
                { expert: "metric", status: "completed" },
                { expert: "log", status: "completed" },
                { expert: "change", status: "failed" },
              ],
            },
          },
        ],
        "completed",
      ),
    );

    expect(model.expertCount).toBe(3);
    expect(model.summary).toContain("3 位专家");
    expect(model.summary).toContain("已完成");
  });

  it("does not describe failed or cancelled runs as completed", () => {
    expect(buildInlineActivityModel(run([], "error")).summary).toBe("执行失败");
    expect(buildInlineActivityModel(run([], "cancelled")).summary).toBe("已停止");
  });

  it("exposes plan steps, structured tool results, and evidence gaps", () => {
    const model = buildInlineActivityModel(
      run(
        [
          {
            type: "agent_event",
            stage: "plan",
            status: "completed",
            payload: {
              todos: ["查询指标", "核对日志"],
              required_evidence: ["指标证据", "日志证据"],
            },
          },
          {
            type: "agent_event",
            stage: "tool_start",
            status: "in_progress",
            payload: { tool: "query_metrics", tool_call_id: "activity-result" },
          },
          {
            type: "tool_event",
            tool: "query_metrics",
            status: "completed",
            payload: {
              tool_call_id: "activity-result",
              result_preview: "status=success；count=1",
              result_fields: [
                { label: "status", value: "success" },
                { label: "count", value: "1" },
                { label: "metric_name", value: "cpu_usage_percent" },
                { label: "statistics.max", value: "91" },
                { label: "memory.total_bytes", value: "16353755136" },
              ],
              result_items: ["CPU 91%"],
            },
          },
          {
            type: "agent_event",
            stage: "verify",
            status: "degraded",
            payload: {
              evidence_count: 1,
              failed_evidence_count: 0,
              confidence: "low",
              gaps: ["缺少日志证据"],
            },
          },
        ],
        "completed",
      ),
    );

    const plan = model.feed.find((item) => item.id === "plan");
    const tool = model.feed.find((item) => item.id === "tool:activity-result");
    const verify = model.feed.find((item) => item.id === "verify");
    expect(plan?.details).toEqual(
      expect.arrayContaining([
        { label: "计划步骤", value: "1. 查询指标；2. 核对日志" },
        { label: "所需证据", value: "指标证据；日志证据" },
      ]),
    );
    expect(tool?.details).toEqual(
      expect.arrayContaining([
        { label: "结果摘要", value: "status=success；count=1" },
        { label: "结果状态", value: "success" },
        { label: "指标名称", value: "cpu_usage_percent" },
        { label: "最大值", value: "91" },
        { label: "内存总量（字节）", value: "16353755136" },
        { label: "结果条目", value: "CPU 91%" },
      ]),
    );
    expect(verify?.details).toEqual(
      expect.arrayContaining([
        { label: "成功证据", value: "1" },
        { label: "置信度", value: "low" },
        { label: "缺少证据", value: "缺少日志证据" },
      ]),
    );
  });
});
