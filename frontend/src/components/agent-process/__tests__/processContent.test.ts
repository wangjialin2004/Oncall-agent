import { describe, expect, it } from "vitest";

import type { TimelineEvent } from "../../../types/events";
import { presentEventDetails } from "../processContent";

function section(event: TimelineEvent, id: string) {
  const result = presentEventDetails(event).find((item) => item.id === id);
  if (!result) {
    throw new Error(`Missing section: ${id}`);
  }
  return result;
}

describe("processContent", () => {
  it("keeps technical ids out of the business result section", () => {
    const event: TimelineEvent = {
      type: "tool_event",
      tool: "delegate_to_expert",
      status: "completed",
      evidence_id: "call-9",
      trace_id: "trace-1",
      span_id: "tool:call-9",
      payload: {
        arguments: { expert: "metric", subtask: "核对 CPU 告警" },
        result: { expert: "metric", status: "completed", answer: "当前无 firing 告警" },
      },
    };

    expect(section(event, "result").summary).toContain("当前无 firing 告警");
    expect(section(event, "result").fields).not.toContainEqual(
      expect.objectContaining({ label: "Trace ID" }),
    );
    expect(section(event, "technical").fields).toContainEqual({ label: "Trace ID", value: "trace-1" });
  });

  it("presents context evidence without dumping nested JSON", () => {
    const event: TimelineEvent = {
      type: "tool_event",
      tool: "context_read",
      status: "completed",
      payload: {
        result: {
          section: "evidence",
          data: {
            tool_summaries: [
              {
                tool: "delegate_to_expert",
                status: "success",
                note: JSON.stringify({ expert: "metric", subtask: "核对 checkout-api CPU" }),
              },
            ],
            observed_facts: [],
          },
        },
      },
    };

    const result = section(event, "result");
    expect(result.fields).toContainEqual({ label: "分区", value: "证据" });
    expect(result.fields).toContainEqual({ label: "工具摘要", value: "1 条" });
    expect(result.items.join(" ")).toContain("核对 checkout-api CPU");
    expect(result.raw).toBeUndefined();
  });

  it("presents recalled experience as structured readable fields", () => {
    const event: TimelineEvent = {
      type: "tool_event",
      tool: "recall_experience",
      status: "completed",
      payload: {
        result: [
          "历史经验仅供参考，必须先用当前证据验证后再采信。",
          "- experience_id: exp-1",
          "  confidence: 0.80",
          "  similarity: 0.60",
          "  symptoms: payment-service 内存使用率超过 85%",
          "> 鈿狅笍 璇佹嵁鑷检锛氱疆淇″害 medium",
          "  verified_root_cause: 堆内存持续增长",
          "  effective_resolution: 补充实时指标并核对 OOM 日志",
          "  evidence_summary: 历史指标和日志一致",
        ].join("\n"),
      },
    };

    const result = section(event, "result");
    expect(result.summary).toBe("召回 1 条历史经验，仅供参考，需以当前证据复核。");
    expect(result.fields).toContainEqual({ label: "经验 ID", value: "exp-1" });
    expect(result.fields).toContainEqual({ label: "置信度", value: "0.80" });
    expect(result.fields).toContainEqual({ label: "相似度", value: "0.60" });
    expect(result.items).toContain("症状：payment-service 内存使用率超过 85%");
    expect(result.items).toContain("历史根因：堆内存持续增长");
    expect(JSON.stringify(result)).not.toMatch(/鈿|璇佹嵁|>\s|^-\s/m);
  });

  it("keeps a clear no-hit message for recalled experience", () => {
    const event: TimelineEvent = {
      type: "tool_event",
      tool: "recall_experience",
      status: "completed",
      payload: { result: "未命中可复用的历史诊断经验。" },
    };

    const result = section(event, "result");
    expect(result.summary).toBe("未命中可复用的历史诊断经验。");
    expect(result.fields).toEqual([]);
    expect(result.items).toEqual([]);
  });

  it("labels anti-pattern experience and limits structured recall to three records", () => {
    const records = Array.from({ length: 4 }, (_, index) =>
      [
        `- ${index === 0 ? "[反模式/勿重复] " : ""}experience_id: exp-${index + 1}`,
        "  confidence: 0.7",
        "  similarity: 0.5",
        `  symptoms: 症状 ${index + 1}`,
        `  ${index === 0 ? "dead_path" : "verified_root_cause"}: 路径 ${index + 1}`,
        `  ${index === 0 ? "guidance" : "effective_resolution"}: 建议 ${index + 1}`,
      ].join("\n"),
    );
    const event: TimelineEvent = {
      type: "tool_event",
      tool: "recall_experience",
      status: "completed",
      payload: { result: ["历史经验仅供参考。", ...records].join("\n") },
    };

    const result = section(event, "result");
    expect(result.summary).toBe("召回 4 条历史经验，仅供参考，需以当前证据复核。");
    expect(result.items).toContain("反模式：勿重复此历史路径");
    expect(result.items).toContain("历史无效路径：路径 1");
    expect(result.fields).toContainEqual({ label: "经验 ID 3", value: "exp-3" });
    expect(result.fields).not.toContainEqual(expect.objectContaining({ value: "exp-4" }));
    expect(result.remaining).toBeGreaterThan(0);
  });
});
