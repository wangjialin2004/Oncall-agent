import { useMemo, useState } from "react";
import { Activity, CheckCircle2, CircleAlert, GitBranch, Wrench } from "lucide-react";

import type { AgentRun, TimelineEvent } from "../types/events";
import { ProcessOverview } from "./agent-process/ProcessOverview";
import { ProcessTimeline } from "./agent-process/ProcessTimeline";
import { buildProcessPanelModel } from "./agent-process/processModel";

type FeedbackHandler = (kind: "adopted" | "corrected", actualRootCause?: string) => void;
type DistillHandler = (action: "confirm" | "reject") => void;
type ConfirmSuggestionHandler = (actionId: string) => void;

type AgentProcessPanelProps = {
  run: AgentRun;
  onFeedback?: FeedbackHandler;
  onDistill?: DistillHandler;
  onConfirmSuggestion?: ConfirmSuggestionHandler;
};

const statusLabels: Record<string, string> = {
  idle: "待命",
  pending: "等待中",
  running: "运行中",
  in_progress: "进行中",
  started: "已启动",
  completed: "已完成",
  success: "成功",
  ok: "成功",
  failed: "失败",
  failure: "失败",
  degraded: "降级",
  error: "错误",
  timeout: "已超时",
  timed_out: "已超时",
  cancelled: "已取消",
  canceled: "已取消",
  skipped: "已跳过",
  partial: "部分完成",
  evidence_insufficient: "证据不足",
  root_cause_ready: "根因已就绪",
};

const routeLabels: Record<string, string> = {
  knowledge: "知识问答",
  knowledge_qa: "知识问答",
  rag: "知识问答",
  metric: "告警/指标",
  metric_alert: "告警/指标",
  metrics: "告警/指标",
  log: "日志分析",
  logs: "日志分析",
  log_analysis: "日志分析",
  change: "变更/发布",
  changes: "变更/发布",
  release: "变更/发布",
  diagnosis: "综合诊断",
  diagnose: "综合诊断",
  clarify: "待澄清",
  clarification: "待澄清",
  unknown: "未知",
  error: "错误",
  fallback: "兜底路径",
};

const modeLabels: Record<string, string> = {
  auto: "自动",
  rag: "知识库",
  knowledge: "知识库",
  diagnosis: "综合诊断",
};

const agentLabels: Record<string, string> = {
  router: "路由分发",
  harness: "统一调度",
  planner: "计划生成",
  expert: "领域专家",
  retriever: "知识检索",
  knowledge_expert: "知识问答专家",
  knowledge_retrieval: "知识检索专家",
  knowledge: "知识问答专家",
  metric_expert: "告警/指标专家",
  metric: "告警/指标专家",
  log_expert: "日志分析专家",
  log: "日志分析专家",
  change_expert: "变更/发布专家",
  change: "变更/发布专家",
  diagnosis: "综合诊断专家",
  diagnose_expert: "综合诊断专家",
};

const toolLabels: Record<string, string> = {
  retrieve_knowledge: "知识库检索",
  query_prometheus_alerts: "Prometheus 告警查询",
  query_metrics: "指标查询",
  query_prometheus_range: "指标区间查询",
  search_app_logs: "应用日志查询",
  query_logs: "日志查询",
  query_cls_logs: "CLS 日志查询",
  query_recent_changes: "近期变更查询",
  lookup_service_knowledge: "服务知识查询",
  recall_experience: "历史经验召回",
  check_redis_health: "Redis 健康检查",
  get_current_time: "获取当前时间",
  delegate_to_expert: "专家委派",
  context_read: "读取上下文",
  context_write: "写入上下文",
};

const stageLabels: Record<string, string> = {
  route: "路由识别",
  routing: "路由识别",
  context: "上下文准备",
  planning: "计划生成",
  plan: "规划",
  model_decision: "模型决策",
  decision: "模型决策",
  model_closing: "模型收尾",
  closing: "模型收尾",
  report: "报告输出",
  start: "开始",
  started: "已启动",
  verify: "证据自检",
  verification: "证据自检",
  budget: "预算控制",
  no_progress: "无进展检测",
  complete: "完成",
  completed: "已完成",
  error: "出错",
  failed: "失败",
  log_pipeline: "日志预处理",
  log_mapreduce: "日志摘要",
  log_summary: "日志摘要",
  clarify_missing_params: "补充参数",
  timeout_fallback: "超时降级",
  step_timeout: "步骤超时",
  delegate_start: "专家委派",
  fallback_start: "兜底开始",
  fallback_complete: "兜底完成",
  fallback_timeout: "兜底超时",
  knowledge_fallback_error: "知识兜底异常",
  knowledge_fallback_empty: "知识兜底为空",
  raw_vector_fallback_complete: "向量兜底完成",
  checkpoint_resume: "检查点恢复",
  checkpoint_conservative_close: "检查点保守收口",
};

const argLabels: Record<string, string> = {
  keyword: "关键词",
  level: "日志级别",
  limit: "条数上限",
  service: "服务",
  service_name: "服务",
  target: "目标",
  query: "查询",
  time_window: "时间窗口",
  window: "时间窗口",
  start: "开始时间",
  end: "结束时间",
  expert: "专家",
  subtask: "子任务",
  metric: "指标",
  metric_name: "指标",
  top_k: "TopK",
  path: "路径",
  host: "主机",
  namespace: "命名空间",
};

type PlanItemStatus = "done" | "running" | "pending" | "blocked";

type PlanItem = {
  id: string;
  text: string;
  status: PlanItemStatus;
  matchedTools: string[];
};

type StepCard = {
  id: string;
  index: number;
  kind: "route" | "plan" | "expert" | "tool" | "verify" | "decision" | "complete" | "other";
  title: string;
  status: string;
  summary: string;
  durationLabel: string;
  keyPairs: Array<[string, string]>;
  lists: Array<{ title: string; items: string[] }>;
  planItems?: PlanItem[];
  resultSummary?: string;
  resultPairs?: Array<[string, string]>;
  resultItems?: string[];
};

function labelFor(value: string | undefined, labels: Record<string, string>) {
  if (!value) {
    return "";
  }
  return labels[value] || value;
}

function payloadString(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function asStringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => String(item)).filter((item) => item.trim().length > 0)
    : [];
}

function formatDuration(ms: number | undefined): string {
  if (ms === undefined || Number.isNaN(ms)) {
    return "";
  }
  if (ms < 1000) {
    return `${Math.round(ms)} ms`;
  }
  return `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)} s`;
}

function humanizeToolName(tool: string | undefined): string {
  if (!tool) {
    return "工具执行";
  }
  return labelFor(tool, toolLabels) || tool.split("_").join(" ");
}

function humanizeText(input: string): string {
  let text = input.trim();
  if (!text) {
    return "";
  }

  // Whole-sentence English summaries first.
  const sentenceMap: Array<[RegExp, string]> = [
    [/^created lightweight investigation plan\.?$/i, "已生成轻量排查计划。"],
    [/^created investigation plan\.?$/i, "已生成排查计划。"],
    [/^plan created\.?$/i, "计划已生成。"],
    [/^routing completed\.?$/i, "路由已完成。"],
    [/^verification completed\.?$/i, "证据自检完成。"],
    [/^evidence insufficient\.?$/i, "证据不足。"],
    [/^fallback to knowledge\.?$/i, "已降级到知识库。"],
  ];
  for (const [pattern, replacement] of sentenceMap) {
    if (pattern.test(text)) {
      return replacement;
    }
  }

  // Longer tokens first to avoid partial replacements.
  const tokenMap: Array<[RegExp, string]> = [
    [/\bquery_prometheus_alerts\b/gi, "Prometheus 告警查询"],
    [/\bquery_prometheus_range\b/gi, "指标区间查询"],
    [/\blookup_service_knowledge\b/gi, "服务知识查询"],
    [/\bretrieve_knowledge\b/gi, "知识库检索"],
    [/\bquery_recent_changes\b/gi, "近期变更查询"],
    [/\bsearch_app_logs\b/gi, "应用日志查询"],
    [/\brecall_experience\b/gi, "历史经验召回"],
    [/\bcheck_redis_health\b/gi, "Redis 健康检查"],
    [/\bget_current_time\b/gi, "获取当前时间"],
    [/\bcontext_read\b/gi, "读取上下文"],
    [/\bcontext_write\b/gi, "写入上下文"],
    [/\bdelegate_to_expert\b/gi, "专家委派"],
    [/\bquery_metrics\b/gi, "指标查询"],
    [/\bquery_logs\b/gi, "日志查询"],
    [/\bquery_cls_logs\b/gi, "CLS 日志查询"],
    [/\blog_expert\b/gi, "日志分析专家"],
    [/\bmetric_expert\b/gi, "告警/指标专家"],
    [/\bchange_expert\b/gi, "变更/发布专家"],
    [/\bknowledge_expert\b/gi, "知识问答专家"],
    [/\bstep_timeout\b/gi, "步骤超时"],
    [/\blightweight investigation plan\b/gi, "轻量排查计划"],
    [/\binvestigation plan\b/gi, "排查计划"],
    [/\brequired evidence\b/gi, "要求证据"],
    [/\broot cause\b/gi, "根因"],
    [/\bcreated\b/gi, "已创建"],
    [/\bharness\b/gi, "统一调度"],
    [/\brouter\b/gi, "路由分发"],
    [/\bdiagnosis\b/gi, "综合诊断"],
    [/\bdelegate\b/gi, "专家委派"],
    [/\bverify(?:ication)?\b/gi, "证据自检"],
    [/\bfallback\b/gi, "兜底"],
    [/\btimeout\b/gi, "超时"],
    // bare expert/domain tokens in Chinese context
    [/(^|[\s,，:：/（(])log(?=[\s,，:：/）)专家焦点]|$)/gi, "$1日志分析"],
    [/(^|[\s,，:：/（(])metric(?=[\s,，:：/）)专家]|$)/gi, "$1告警/指标"],
    [/(^|[\s,，:：/（(])change(?=[\s,，:：/）)专家]|$)/gi, "$1变更/发布"],
    [/(^|[\s,，:：/（(])knowledge(?=[\s,，:：/）)专家]|$)/gi, "$1知识问答"],
  ];
  for (const [pattern, replacement] of tokenMap) {
    text = text.replace(pattern, replacement);
  }

  return text.replace(/\s{2,}/g, " ").trim();
}

function tryParseJson(value: unknown): unknown {
  if (typeof value !== "string") {
    return value;
  }
  const text = value.trim();
  if (!text || (text[0] !== "{" && text[0] !== "[")) {
    return value;
  }
  try {
    return JSON.parse(text);
  } catch {
    return value;
  }
}

function shortText(value: unknown, max = 180): string {
  if (value === null || value === undefined) {
    return "";
  }
  const text = humanizeText(String(value));
  if (!text) {
    return "";
  }
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function formatScalar(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "";
  }
  if (typeof value === "boolean") {
    return value ? "是" : "否";
  }
  if (typeof value === "number") {
    return String(value);
  }
  if (typeof value === "string") {
    return humanizeText(value);
  }
  return "";
}

function objectEntries(value: unknown): Array<[string, unknown]> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return [];
  }
  return Object.entries(value as Record<string, unknown>);
}

function formatObjectPairs(
  value: unknown,
  labels: Record<string, string> = {},
  limit = 12,
): Array<[string, string]> {
  const parsed = tryParseJson(value);
  return objectEntries(parsed)
    .slice(0, limit)
    .map(([key, raw]): [string, string] => {
      const label = labels[key] || key.split("_").join(" ");
      const nested = tryParseJson(raw);
      if (Array.isArray(nested)) {
        return [
          label,
          nested
            .map((item) => formatScalar(item) || shortText(item, 48))
            .filter(Boolean)
            .join("、"),
        ];
      }
      if (nested && typeof nested === "object") {
        return [label, shortText(JSON.stringify(nested), 100)];
      }
      return [label, formatScalar(nested) || shortText(nested, 100)];
    })
    .filter(([, text]) => Boolean(text));
}

function unwrapNestedJson(value: unknown, depth = 0): unknown {
  if (depth > 3) {
    return value;
  }
  const parsed = tryParseJson(value);
  if (parsed !== value) {
    return unwrapNestedJson(parsed, depth + 1);
  }
  return parsed;
}

function formatToolNote(value: unknown): string {
  const parsed = unwrapNestedJson(value);
  if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
    const data = parsed as Record<string, unknown>;
    const expert =
      formatScalar(data.expert) ||
      labelFor(String(data.expert || ""), agentLabels) ||
      "";
    const status = formatScalar(data.status);
    const subtask = shortText(data.subtask, 120);
    const answer = shortText(data.answer || data.summary || data.message, 120);
    const parts = [
      expert ? `专家 ${labelFor(String(data.expert || ""), agentLabels) || expert}` : "",
      status ? `状态 ${status}` : "",
      subtask ? `任务 ${subtask}` : "",
      answer ? `结论 ${answer}` : "",
    ].filter(Boolean);
    if (parts.length > 0) {
      return parts.join(" · ");
    }
  }
  return shortText(parsed, 160);
}

function summarizeToolSummaryRow(row: Record<string, unknown>): string {
  const tool = humanizeToolName(String(row.tool || row.name || "工具"));
  const status = formatScalar(row.status) || String(row.status || "");
  const latency =
    typeof row.latency_ms === "number" ? formatDuration(row.latency_ms) : formatScalar(row.latency_ms);
  const note = formatToolNote(row.note ?? row.summary ?? row.content);
  return [tool, status, latency, note].filter(Boolean).join(" · ");
}

function summarizeObservedFactRow(row: Record<string, unknown>): string {
  const source = humanizeToolName(String(row.source || row.tool || "来源"));
  const status = formatScalar(row.status) || String(row.status || "");
  const facts = Array.isArray(row.facts)
    ? row.facts.map((item) => shortText(item, 80)).filter(Boolean).join("；")
    : shortText(row.facts, 120);
  return [source, status, facts].filter(Boolean).join(" · ");
}

function summarizeContextReadResult(data: Record<string, unknown>): {
  summary: string;
  pairs: Array<[string, string]>;
  items: string[];
} {
  const section = formatScalar(data.section) || String(data.section || "");
  const sectionLabel: Record<string, string> = {
    evidence: "证据",
    intent: "意图",
    working: "工作计划",
    conversation: "会话",
    output: "输出约定",
  };
  const pairs: Array<[string, string]> = [];
  if (section) {
    pairs.push(["分区", sectionLabel[section] || section]);
  }

  const block = unwrapNestedJson(data.data);
  if (!block || typeof block !== "object" || Array.isArray(block)) {
    const viewText = shortText(data.view || data.snapshot || block, 240);
    return {
      summary: viewText || (section ? `已读取 ${sectionLabel[section] || section}` : "已读取上下文"),
      pairs,
      items: [],
    };
  }

  const body = block as Record<string, unknown>;
  const items: string[] = [];

  if (Array.isArray(body.tool_summaries) && body.tool_summaries.length > 0) {
    pairs.push(["工具摘要", `${body.tool_summaries.length} 条`]);
    for (const row of body.tool_summaries.slice(0, 6)) {
      if (row && typeof row === "object") {
        items.push(summarizeToolSummaryRow(row as Record<string, unknown>));
      } else {
        items.push(shortText(row, 140));
      }
    }
  }
  if (Array.isArray(body.observed_facts) && body.observed_facts.length > 0) {
    pairs.push(["已观察事实", `${body.observed_facts.length} 条`]);
    for (const row of body.observed_facts.slice(0, 4)) {
      if (row && typeof row === "object") {
        items.push(summarizeObservedFactRow(row as Record<string, unknown>));
      } else {
        items.push(shortText(row, 140));
      }
    }
  }
  if (Array.isArray(body.evidence_gaps) && body.evidence_gaps.length > 0) {
    pairs.push(["证据缺口", `${body.evidence_gaps.length} 条`]);
  }
  if (Array.isArray(body.model_notes) && body.model_notes.length > 0) {
    pairs.push(["模型笔记", `${body.model_notes.length} 条`]);
  }
  if (body.current_goal) {
    pairs.push(["当前目标", shortText(body.current_goal, 120)]);
  }
  if (body.current_question) {
    pairs.push(["当前问题", shortText(body.current_question, 120)]);
  }
  if (body.plan) {
    pairs.push(["计划", shortText(body.plan, 120)]);
  }
  if (Array.isArray(body.pending_steps) && body.pending_steps.length > 0) {
    pairs.push(["待执行", body.pending_steps.slice(0, 3).map((item) => shortText(item, 40)).join("、")]);
  }
  if (Array.isArray(body.completed_steps) && body.completed_steps.length > 0) {
    pairs.push(["已完成", `${body.completed_steps.length} 步`]);
  }

  const summary =
    pairs.map(([k, v]) => `${k}：${v}`).join(" · ") ||
    (section ? `已读取 ${sectionLabel[section] || section}` : "已读取上下文");
  return { summary, pairs, items };
}

function summarizeDelegateResult(data: Record<string, unknown>): {
  summary: string;
  pairs: Array<[string, string]>;
  items: string[];
} {
  const pairs: Array<[string, string]> = [];
  const expert = String(data.expert || "");
  if (expert) {
    pairs.push(["专家", labelFor(expert, agentLabels) || expert]);
  }
  if (data.status !== undefined) {
    pairs.push(["状态", formatScalar(data.status) || String(data.status)]);
  }
  if (data.subtask) {
    pairs.push(["子任务", shortText(data.subtask, 160)]);
  }
  if (data.error) {
    pairs.push(["错误", shortText(data.error, 140)]);
  }
  const answer = shortText(data.answer, 200);
  if (answer) {
    pairs.push(["结论", answer]);
  }
  const items = answer ? [answer] : [];
  const summary = pairs.map(([k, v]) => `${k}：${v}`).join(" · ");
  return { summary, pairs, items };
}

function summarizeToolResult(value: unknown): {
  summary: string;
  pairs: Array<[string, string]>;
  items: string[];
  rawPreview?: string;
} {
  const parsed = unwrapNestedJson(value);
  if (parsed === undefined || parsed === null || parsed === "") {
    return { summary: "", pairs: [], items: [] };
  }
  if (typeof parsed !== "object") {
    return { summary: shortText(parsed, 240), pairs: [], items: [] };
  }
  if (Array.isArray(parsed)) {
    return {
      summary: `共 ${parsed.length} 条结果`,
      pairs: [],
      items: parsed.slice(0, 6).map((item) => shortText(item, 120)),
    };
  }

  const data = parsed as Record<string, unknown>;

  // context_read: { section, data } or { view } / { snapshot }
  if (data.section !== undefined || data.view !== undefined || data.snapshot !== undefined) {
    const contextResult = summarizeContextReadResult(data);
    return {
      summary: contextResult.summary,
      pairs: contextResult.pairs,
      items: contextResult.items,
    };
  }

  // delegate_to_expert: { expert, status, subtask, answer }
  if (data.expert !== undefined && (data.subtask !== undefined || data.answer !== undefined)) {
    const delegateResult = summarizeDelegateResult(data);
    return {
      summary: delegateResult.summary,
      pairs: delegateResult.pairs,
      items: delegateResult.items,
    };
  }

  const pairs: Array<[string, string]> = [];
  const push = (label: string, raw: unknown) => {
    const nested = unwrapNestedJson(raw);
    if (nested && typeof nested === "object" && !Array.isArray(nested)) {
      const text = formatToolNote(nested);
      if (text) {
        pairs.push([label, text]);
      }
      return;
    }
    const text = formatScalar(nested) || shortText(nested, 100);
    if (text) {
      pairs.push([label, text]);
    }
  };
  push("状态", data.status ?? data.success ?? data.ok);
  push("来源", data.source);
  push("条数", data.total ?? data.count);
  push("服务", data.service ?? data.service_name);
  push("说明", data.message ?? data.summary);
  push("专家", data.expert ? labelFor(String(data.expert), agentLabels) || data.expert : "");
  push("子任务", data.subtask);
  if (data.source_available === false) {
    push("数据源", "未接入");
  }
  if (data.error) {
    push("错误", data.error);
  }

  let items: string[] = [];
  if (Array.isArray(data.tool_summaries) && data.tool_summaries.length > 0) {
    items = data.tool_summaries.slice(0, 6).map((row) =>
      row && typeof row === "object"
        ? summarizeToolSummaryRow(row as Record<string, unknown>)
        : shortText(row, 140),
    );
  } else if (Array.isArray(data.observed_facts) && data.observed_facts.length > 0) {
    items = data.observed_facts.slice(0, 6).map((row) =>
      row && typeof row === "object"
        ? summarizeObservedFactRow(row as Record<string, unknown>)
        : shortText(row, 140),
    );
  } else {
    for (const key of ["logs", "alerts", "changes", "items", "results", "experiences"]) {
      if (Array.isArray(data[key]) && data[key].length > 0) {
        items = (data[key] as unknown[]).slice(0, 6).map((item) => {
          if (!item || typeof item !== "object") {
            return shortText(item, 140);
          }
          const row = item as Record<string, unknown>;
          return (
            shortText(
              row.summary ||
                row.message ||
                row.alertname ||
                row.change_id ||
                row.content ||
                row.symptom ||
                formatToolNote(row),
              140,
            ) || "—"
          );
        });
        break;
      }
    }
  }

  if (!items.length && data.answer) {
    const answer = shortText(data.answer, 200);
    if (answer) {
      items = [answer];
    }
  }

  const summary = pairs.map(([k, v]) => `${k}：${v}`).join(" · ");
  return {
    summary,
    pairs,
    items,
    rawPreview:
      pairs.length === 0 && items.length === 0
        ? shortText(JSON.stringify(data, null, 2), 420)
        : undefined,
  };
}

function expertLabelFromEvent(event: TimelineEvent) {
  const payload = (event.payload ?? {}) as Record<string, unknown>;
  const argumentsPayload =
    payload.arguments && typeof payload.arguments === "object"
      ? (payload.arguments as Record<string, unknown>)
      : {};
  const expert =
    payloadString(payload.delegated_expert) ||
    payloadString(payload.expert) ||
    payloadString(argumentsPayload.expert);
  if (!expert) {
    return "";
  }
  return agentLabels[expert] || agentLabels[`${expert}_expert`] || expert;
}

function isDelegateEvent(event: TimelineEvent) {
  // Only the start of a delegation becomes an "expert" card.
  // Completed `delegate_to_expert` tool_events still go through the tool path so
  // their structured result can be summarized instead of dropped.
  if (event.stage === "delegate_start") {
    return true;
  }
  return event.type !== "tool_event" && event.tool === "delegate_to_expert";
}

function eventIcon(kind: StepCard["kind"], status: string) {
  if (kind === "route" || kind === "expert") {
    return <GitBranch size={16} aria-hidden="true" />;
  }
  if (kind === "tool") {
    return <Wrench size={16} aria-hidden="true" />;
  }
  if (status === "completed" || status === "success" || status === "ok") {
    return <CheckCircle2 size={16} aria-hidden="true" />;
  }
  if (status === "failed" || status === "degraded" || status === "error" || status === "timeout") {
    return <CircleAlert size={16} aria-hidden="true" />;
  }
  return <Activity size={16} aria-hidden="true" />;
}

function statusText(status: string, route?: string) {
  if (route) {
    return labelFor(route, routeLabels) || "已识别";
  }
  return labelFor(status, statusLabels) || labelFor(status, stageLabels) || status || "执行中";
}

function extractPlanTodos(events: TimelineEvent[]): string[] {
  for (const event of events) {
    if (event.stage === "plan" || event.stage === "planning") {
      const todos = asStringList((event.payload as Record<string, unknown> | undefined)?.todos);
      if (todos.length > 0) {
        return todos.map((item) => humanizeText(item));
      }
    }
  }
  return [];
}

function toolTokens(tool?: string): string[] {
  if (!tool) {
    return [];
  }
  const human = humanizeToolName(tool).toLowerCase();
  return [tool.toLowerCase(), human, tool.split("_").join(" ").toLowerCase()];
}

function matchPlanItem(todo: string, tools: string[], experts: string[]): PlanItemStatus {
  const lower = todo.toLowerCase();
  const hitTool = tools.some((tool) => {
    const tokens = toolTokens(tool);
    return tokens.some((token) => token && (lower.includes(token) || token.includes(lower.slice(0, 8))));
  });
  if (hitTool) {
    return "done";
  }
  if (experts.some((expert) => lower.includes(expert.toLowerCase()) || lower.includes("专家"))) {
    // expert entered but tool match unknown — treat as running/partial progress
    return "running";
  }
  if (/自检|定稿|结论/.test(todo)) {
    return "pending";
  }
  return "pending";
}

function buildPlanProgress(run: AgentRun): PlanItem[] {
  const todos = extractPlanTodos(run.events);
  if (todos.length === 0) {
    return [];
  }
  const tools = run.events
    .filter((event) => event.type === "tool_event" && event.tool)
    .map((event) => String(event.tool));
  const experts = run.events
    .filter((event) => isDelegateEvent(event))
    .map((event) => expertLabelFromEvent(event))
    .filter(Boolean);

  const items = todos.map((text, index) => {
    const matchedTools = tools.filter((tool) => {
      const lower = text.toLowerCase();
      return toolTokens(tool).some((token) => token && lower.includes(token));
    });
    let status = matchPlanItem(text, tools, experts);
    if (matchedTools.length > 0) {
      status = "done";
    }
    return {
      id: `plan-${index}`,
      text,
      status,
      matchedTools: matchedTools.map((tool) => humanizeToolName(tool)),
    };
  });

  if (run.status === "completed") {
    return items.map((item) => ({ ...item, status: "done" }));
  }

  // Plans are ordered. Once a later item has advanced, earlier items must not
  // fall back to pending just because their text did not directly name a tool.
  let furthestAdvanced = -1;
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (items[index].status === "done" || items[index].status === "running") {
      furthestAdvanced = index;
      break;
    }
  }
  for (let index = 0; index < furthestAdvanced; index += 1) {
    items[index] = { ...items[index], status: "done" };
  }

  if (run.status === "running" || run.status === "idle") {
    const hasRunningItem = items.some((item) => item.status === "running");
    if (!hasRunningItem) {
      const firstPending = items.findIndex((item) => item.status === "pending");
      if (firstPending >= 0) {
        items[firstPending] = { ...items[firstPending], status: "running" };
      }
    }
  }
  return items;
}

function buildStepCards(run: AgentRun): StepCard[] {
  const planProgress = buildPlanProgress(run);
  const cards: StepCard[] = [];
  let stepNo = 1;

  for (const [index, event] of run.events.entries()) {
    const payload = (event.payload ?? {}) as Record<string, unknown>;
    const durationLabel = formatDuration(event.duration_ms);
    const lists: Array<{ title: string; items: string[] }> = [];
    const keyPairs: Array<[string, string]> = [];

    // ---- route ----
    if (event.type === "route_event") {
      keyPairs.push(["路径", labelFor(event.route, routeLabels) || event.route || "未知"]);
      if (event.summary) {
        keyPairs.push(["依据", humanizeText(event.summary)]);
      }
      cards.push({
        id: `route-${index}`,
        index: stepNo++,
        kind: "route",
        title: "1. 路由识别",
        status: event.status || "completed",
        summary: `判定为 ${labelFor(event.route, routeLabels) || "未知路径"}`,
        durationLabel,
        keyPairs,
        lists,
      });
      continue;
    }

    // ---- plan ----
    if (event.stage === "plan" || event.stage === "planning") {
      const evidence = asStringList(payload.required_evidence).map((item) => humanizeText(item));
      const gaps = asStringList(payload.gaps).map((item) => humanizeText(item));
      if (evidence.length) {
        lists.push({ title: "要求证据", items: evidence });
      }
      if (Array.isArray(payload.required_params) && payload.required_params.length > 0) {
        lists.push({
          title: "待补充参数",
          items: payload.required_params.map((item, paramIndex) => {
            if (!item || typeof item !== "object") {
              return shortText(item, 120) || `参数 ${paramIndex + 1}`;
            }
            const data = item as Record<string, unknown>;
            const prompt = formatScalar(data.prompt || data.name) || `参数 ${paramIndex + 1}`;
            const reason = formatScalar(data.reason);
            return reason ? `${prompt}：${reason}` : prompt;
          }),
        });
      }
      if (gaps.length) {
        lists.push({ title: "已知缺口", items: gaps });
      }
      if (payload.confidence !== undefined && payload.confidence !== null && payload.confidence !== "") {
        keyPairs.push(["置信度", String(payload.confidence)]);
      }
      if (payload.max_steps !== undefined) {
        keyPairs.push(["最大步数", String(payload.max_steps)]);
      }
      if (payload.tool_count !== undefined) {
        keyPairs.push(["可用工具", String(payload.tool_count)]);
      }
      if (payload.history_turns !== undefined) {
        keyPairs.push(["历史轮数", String(payload.history_turns)]);
      }
      const doneCount = planProgress.filter((item) => item.status === "done").length;
      if (planProgress.length > 0) {
        keyPairs.push(["计划进度", `${doneCount}/${planProgress.length}`]);
      }

      cards.push({
        id: `plan-${index}`,
        index: stepNo++,
        kind: "plan",
        title: "2. 制定计划",
        status: event.status || "completed",
        summary: humanizeText(event.summary || "") || "已生成排查计划，并按步骤执行。",
        durationLabel,
        keyPairs,
        lists,
        planItems: planProgress,
      });
      continue;
    }

    // ---- expert ----
    if (isDelegateEvent(event)) {
      const expert = expertLabelFromEvent(event) || "专项专家";
      keyPairs.push(["专家", expert]);
      if (payload.subtask) {
        keyPairs.push(["子任务", shortText(payload.subtask, 180)]);
      }
      if (payload.reason) {
        keyPairs.push(["原因", shortText(payload.reason, 160)]);
      }
      cards.push({
        id: `expert-${index}`,
        index: stepNo++,
        kind: "expert",
        title: `进入专家：${expert}`,
        status: event.status || "in_progress",
        summary: humanizeText(event.summary || "") || `交由 ${expert} 继续取证`,
        durationLabel,
        keyPairs,
        lists,
      });
      continue;
    }

    // ---- tool ----
    if (event.type === "tool_event") {
      const toolName = humanizeToolName(event.tool);
      const argPairs = formatObjectPairs(payload.arguments, argLabels);
      for (const pair of argPairs) {
        keyPairs.push(pair);
      }
      if (payload.subtask) {
        keyPairs.push(["子任务", shortText(payload.subtask, 160)]);
      }
      const result = summarizeToolResult(payload.result);
      cards.push({
        id: `tool-${index}`,
        index: stepNo++,
        kind: "tool",
        title: `执行：${toolName}`,
        status: event.status || "completed",
        summary: humanizeText(event.summary || "") || `${toolName} 已执行`,
        durationLabel,
        keyPairs,
        lists,
        resultSummary: result.summary || result.rawPreview,
        resultPairs: result.pairs,
        resultItems: result.items,
      });
      continue;
    }

    // ---- verify ----
    if (event.stage === "verify" || event.stage === "verification") {
      const gaps = asStringList(payload.gaps).map((item) => humanizeText(item));
      if (gaps.length) {
        lists.push({ title: "自检缺口", items: gaps });
      }
      if (payload.evidence_count !== undefined) {
        keyPairs.push(["成功证据", String(payload.evidence_count)]);
      }
      if (payload.failed_evidence_count !== undefined) {
        keyPairs.push(["失败证据", String(payload.failed_evidence_count)]);
      }
      if (payload.confidence !== undefined && payload.confidence !== null && payload.confidence !== "") {
        keyPairs.push(["置信度", String(payload.confidence)]);
      }
      if (payload.evidence_gap) {
        keyPairs.push(["证据缺口", shortText(payload.evidence_gap, 160)]);
      }
      if (payload.reason) {
        keyPairs.push(["原因", shortText(payload.reason, 160)]);
      }
      cards.push({
        id: `verify-${index}`,
        index: stepNo++,
        kind: "verify",
        title: "证据自检",
        status: event.status || "completed",
        summary: humanizeText(event.summary || "") || "核对证据是否足够支撑结论",
        durationLabel,
        keyPairs,
        lists,
      });
      continue;
    }

    // ---- start / complete / decision / other ----
    if (event.stage === "start" || event.stage === "started") {
      keyPairs.push(["执行体", labelFor(event.agent, agentLabels) || event.agent || "智能体"]);
      cards.push({
        id: `start-${index}`,
        index: stepNo++,
        kind: "other",
        title: `${labelFor(event.agent, agentLabels) || "智能体"}启动`,
        status: event.status || "in_progress",
        summary: humanizeText(event.summary || "") || "开始执行",
        durationLabel,
        keyPairs,
        lists,
      });
      continue;
    }

    if (event.stage === "complete" || event.stage === "completed") {
      if (payload.confidence !== undefined && payload.confidence !== null && payload.confidence !== "") {
        keyPairs.push(["置信度", String(payload.confidence)]);
      }
      if (payload.evidence_count !== undefined) {
        keyPairs.push(["成功证据", String(payload.evidence_count)]);
      }
      cards.push({
        id: `done-${index}`,
        index: stepNo++,
        kind: "complete",
        title: `${labelFor(event.agent, agentLabels) || "本阶段"}完成`,
        status: event.status || "completed",
        summary: humanizeText(event.summary || "") || "本阶段已完成",
        durationLabel,
        keyPairs,
        lists,
      });
      continue;
    }

    if (event.type === "decision_event" || event.stage === "model_decision" || event.stage === "decision") {
      if (payload.reason) {
        keyPairs.push(["决策依据", shortText(payload.reason, 180)]);
      }
      cards.push({
        id: `decision-${index}`,
        index: stepNo++,
        kind: "decision",
        title: "模型决策",
        status: event.status || "completed",
        summary: humanizeText(event.summary || "") || "模型选择下一步动作",
        durationLabel,
        keyPairs,
        lists,
      });
      continue;
    }

    // generic fallback — still show rich payload
    if (payload.confidence !== undefined && payload.confidence !== null && payload.confidence !== "") {
      keyPairs.push(["置信度", String(payload.confidence)]);
    }
    if (payload.subtask) {
      keyPairs.push(["子任务", shortText(payload.subtask, 160)]);
    }
    if (payload.reason) {
      keyPairs.push(["原因", shortText(payload.reason, 160)]);
    }
    const todos = asStringList(payload.todos).map((item) => humanizeText(item));
    if (todos.length) {
      lists.push({ title: "相关步骤", items: todos });
    }
    cards.push({
      id: `other-${index}`,
      index: stepNo++,
      kind: "other",
      title:
        labelFor(event.stage, stageLabels) ||
        labelFor(event.agent, agentLabels) ||
        "执行步骤",
      status: event.status || "in_progress",
      summary: humanizeText(event.summary || "") || "继续执行",
      durationLabel,
      keyPairs,
      lists,
    });
  }

  // renumber titles that hardcode 1./2. if route/plan missing order is fine
  return cards.map((card, index) => ({
    ...card,
    index: index + 1,
    title: card.title.replace(/^\d+\.\s*/, `${index + 1}. `),
  }));
}

function planStatusLabel(status: PlanItemStatus): string {
  if (status === "done") {
    return "已完成";
  }
  if (status === "running") {
    return "进行中";
  }
  if (status === "blocked") {
    return "受阻";
  }
  return "待执行";
}

function KeyPairs({ pairs }: { pairs: Array<[string, string]> }) {
  if (pairs.length === 0) {
    return null;
  }
  return (
    <div className="event-detail-block">
      <span>关键信息</span>
      <dl className="event-kv">
        {pairs.map(([label, value]) => (
          <div key={`${label}-${value}`}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function ItemList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) {
    return null;
  }
  return (
    <div className="event-detail-block">
      <span>{title}</span>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function PlanProgress({ items }: { items: PlanItem[] }) {
  if (items.length === 0) {
    return null;
  }
  return (
    <div className="event-detail-block plan-progress">
      <span>执行清单</span>
      <ol className="plan-checklist">
        {items.map((item, index) => (
          <li key={item.id} className={`plan-item status-${item.status}`}>
            <div className="plan-item-index" aria-hidden="true">
              {index + 1}
            </div>
            <div className="plan-item-body">
              <p>{item.text}</p>
              {item.matchedTools.length > 0 ? (
                <small>对应工具：{item.matchedTools.join("、")}</small>
              ) : null}
            </div>
            <em>{planStatusLabel(item.status)}</em>
          </li>
        ))}
      </ol>
    </div>
  );
}

function StepCardView({ card }: { card: StepCard }) {
  return (
    <li className={`process-step status-${card.status || "idle"} kind-${card.kind}`}>
      <div className="timeline-icon">{eventIcon(card.kind, card.status)}</div>
      <div className="timeline-content">
        <div className="timeline-header">
          <strong>{card.title}</strong>
          <span>
            {statusText(card.status)}
            {card.durationLabel ? ` · ${card.durationLabel}` : ""}
          </span>
        </div>
        {card.summary ? <p>{card.summary}</p> : null}

        <div className="event-highlights">
          {card.planItems ? <PlanProgress items={card.planItems} /> : null}
          <KeyPairs pairs={card.keyPairs} />
          {card.lists.map((list) => (
            <ItemList key={list.title} title={list.title} items={list.items} />
          ))}
          {card.resultSummary || (card.resultPairs && card.resultPairs.length > 0) || (card.resultItems && card.resultItems.length > 0) ? (
            <div className="event-detail-block event-detail-result">
              <span>执行结果</span>
              {card.resultSummary ? <p className="event-result-summary">{card.resultSummary}</p> : null}
              {card.resultPairs && card.resultPairs.length > 0 ? (
                <dl className="event-kv">
                  {card.resultPairs.map(([label, value]) => (
                    <div key={`${label}-${value}`}>
                      <dt>{label}</dt>
                      <dd>{value}</dd>
                    </div>
                  ))}
                </dl>
              ) : null}
              {card.resultItems && card.resultItems.length > 0 ? (
                <ul>
                  {card.resultItems.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </li>
  );
}

function hasPendingDistillDraft(run: AgentRun): boolean {
  const draft = run.distillDraft;
  return Boolean(
    draft?.experience_id &&
      draft.status === "pending" &&
      run.distillStatus !== "confirmed" &&
      run.distillStatus !== "rejected",
  );
}

function FeedbackCard({
  run,
  onFeedback,
  onDistill,
}: {
  run: AgentRun;
  onFeedback?: FeedbackHandler;
  onDistill?: DistillHandler;
}) {
  const [correcting, setCorrecting] = useState(false);
  const [rootCause, setRootCause] = useState("");
  const pendingDraft = hasPendingDistillDraft(run);

  // Unified settled states — one card only, even when both distill + feedback apply.
  if (run.feedback === "adopted" || run.distillStatus === "confirmed") {
    return (
      <div className="panel-card feedback-card" data-testid="feedback-card">
        <span className="label">反馈</span>
        <p>已采纳，将沉淀为长期经验。</p>
      </div>
    );
  }
  if (run.feedback === "corrected") {
    return (
      <div className="panel-card feedback-card" data-testid="feedback-card">
        <span className="label">反馈</span>
        <p>已记录纠正，将沉淀为长期经验。</p>
      </div>
    );
  }

  // Pending auto-distill + diagnosis feedback share one action surface so the
  // panel does not stack two near-identical "采纳" cards.
  if (pendingDraft) {
    return (
      <div className="panel-card feedback-card" data-testid="feedback-card">
        <span className="label">结果反馈</span>
        <p>系统已生成待确认经验草稿。采纳后进入召回；也可纠正根因，或拒绝该草稿。</p>
        {correcting ? (
          <div className="feedback-correct">
            <textarea
              aria-label="纠正根因"
              value={rootCause}
              placeholder="请填写实际根因…"
              onChange={(event) => setRootCause(event.target.value)}
            />
            <div className="feedback-actions">
              <button
                type="button"
                disabled={!rootCause.trim()}
                onClick={() => onFeedback?.("corrected", rootCause.trim())}
              >
                提交纠正
              </button>
              <button type="button" className="ghost" onClick={() => setCorrecting(false)}>
                取消
              </button>
            </div>
          </div>
        ) : (
          <div className="feedback-actions">
            <button type="button" onClick={() => onDistill?.("confirm")}>
              采纳为经验
            </button>
            <button type="button" className="ghost" onClick={() => setCorrecting(true)}>
              纠正
            </button>
            <button type="button" className="ghost" onClick={() => onDistill?.("reject")}>
              拒绝草稿
            </button>
          </div>
        )}
      </div>
    );
  }

  // Draft rejected without diagnosis feedback: still allow adopt/correct, with a note.
  const draftRejectedNote =
    run.distillStatus === "rejected" ? (
      <p className="feedback-note">已拒绝自动蒸馏草稿，仍可对本次诊断给出反馈。</p>
    ) : null;

  return (
    <div className="panel-card feedback-card" data-testid="feedback-card">
      <span className="label">这次诊断有帮助吗？</span>
      {draftRejectedNote}
      {correcting ? (
        <div className="feedback-correct">
          <textarea
            aria-label="纠正根因"
            value={rootCause}
            placeholder="请填写实际根因…"
            onChange={(event) => setRootCause(event.target.value)}
          />
          <div className="feedback-actions">
            <button
              type="button"
              disabled={!rootCause.trim()}
              onClick={() => onFeedback?.("corrected", rootCause.trim())}
            >
              提交纠正
            </button>
            <button type="button" className="ghost" onClick={() => setCorrecting(false)}>
              取消
            </button>
          </div>
        </div>
      ) : (
        <div className="feedback-actions">
          <button type="button" onClick={() => onFeedback?.("adopted")}>
            采纳
          </button>
          <button type="button" className="ghost" onClick={() => setCorrecting(true)}>
            纠正
          </button>
        </div>
      )}
    </div>
  );
}

function describeReplayMode(replayOverride: boolean | null, conservative: boolean): string {
  if (replayOverride === true) {
    return "激进模式：本次主动重放了非白名单工具";
  }
  if (replayOverride === false) {
    return "强制保守：本次明确拒绝重放非白名单工具";
  }
  return conservative ? "保守模式：默认未重放非白名单工具" : "激进模式：按服务端默认重放了工具";
}

function riskLabel(risk: string): string {
  const normalized = risk.toLowerCase();
  if (normalized === "high") return "高风险";
  if (normalized === "medium") return "中风险";
  if (normalized === "low") return "低风险";
  return risk || "未知";
}

function SuggestedActionsCard({
  run,
  onConfirmSuggestion,
}: {
  run: AgentRun;
  onConfirmSuggestion?: ConfirmSuggestionHandler;
}) {
  const actions = run.suggestedActions ?? [];
  if (actions.length === 0) {
    return null;
  }
  const confirmed = new Set(run.confirmedActionIds ?? []);
  const allConfirmed = actions.every((action) => confirmed.has(action.id));

  return (
    <div className="panel-card hitl-card" data-testid="suggested-actions-card">
      <span className="label">建议动作（人工确认）</span>
      <p className="hitl-hint">
        以下为只读建议。确认仅记录审计，不会自动执行重启/回滚/扩缩容。
      </p>
      <ul className="hitl-action-list">
        {actions.map((action) => {
          const isConfirmed = confirmed.has(action.id);
          return (
            <li key={action.id} className={`hitl-action-item${isConfirmed ? " is-confirmed" : ""}`}>
              <div className="hitl-action-main">
                <span className="hitl-action-title">{action.title}</span>
                <span className={`hitl-risk hitl-risk-${action.risk || "low"}`}>
                  {riskLabel(action.risk)}
                </span>
              </div>
              {isConfirmed ? (
                <span className="hitl-confirmed" role="status">
                  已确认（未执行）
                </span>
              ) : (
                <button
                  type="button"
                  className="hitl-confirm-btn"
                  onClick={() => onConfirmSuggestion?.(action.id)}
                >
                  确认建议
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {allConfirmed ? (
        <p className="hitl-footer" role="status">
          全部建议已确认记录。系统未执行任何变更。
        </p>
      ) : null}
    </div>
  );
}

function EscalationCard({ run }: { run: AgentRun }) {
  const escalation = run.escalation;
  if (!escalation) {
    return null;
  }
  const contacts = escalation.contacts ?? [];
  return (
    <div
      className={`panel-card hitl-card escalation-card${
        escalation.configured ? "" : " is-unconfigured"
      }`}
      data-testid="escalation-card"
    >
      <span className="label">升级联系</span>
      {escalation.configured && contacts.length > 0 ? (
        <ul className="hitl-contact-list">
          {contacts.map((contact) => (
            <li key={`${contact.name}-${contact.channel}`}>
              <strong>{contact.name}</strong>
              {contact.channel ? <span> · {contact.channel}</span> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p>{escalation.text || "未配置值班联系人，请走现有 OnCall 升级流程。"}</p>
      )}
    </div>
  );
}

function CheckpointBanner({ run }: { run: AgentRun }) {
  const resume = run.checkpointResume;
  const close = run.checkpointConservativeClose;
  if (!resume && !close) {
    return null;
  }
  return (
    <>
      {resume ? (
        <div
          className="panel-card checkpoint-banner"
          role="status"
          aria-live="polite"
          data-testid="checkpoint-resume-banner"
        >
          <span className="label">检查点恢复</span>
          <p>
            已从步骤 <strong>{resume.resumedFromStep}</strong> 的检查点恢复，共
            <strong> {resume.replayedSteps} </strong>步已落盘。
          </p>
          <p className="checkpoint-banner-meta">
            {describeReplayMode(resume.replayOverride, resume.conservative)}
            {resume.startedAt ? ` · 上次开始于 ${resume.startedAt}` : ""}
          </p>
        </div>
      ) : null}
      {close ? (
        <div
          className="panel-card checkpoint-banner conservative"
          role="status"
          aria-live="polite"
          data-testid="checkpoint-conservative-close-banner"
        >
          <span className="label">保守收口</span>
          <p>
            上一步骤（{close.step}）包含非白名单工具，本次未自动重放，将基于已落盘证据直接收口。
          </p>
        </div>
      ) : null}
    </>
  );
}

export function AgentProcessPanel({
  run,
  onFeedback,
  onDistill,
  onConfirmSuggestion,
}: AgentProcessPanelProps) {
  const cards = useMemo(() => buildStepCards(run), [run]);
  const planItems = useMemo(() => buildPlanProgress(run), [run]);
  const processModel = useMemo(() => buildProcessPanelModel(run), [run]);
  const processPanelV2Enabled = import.meta.env.VITE_AGENT_PROCESS_PANEL_V2 !== "0";
  const donePlan = planItems.filter((item) => item.status === "done").length;
  const toolCount = run.events.filter((event) => event.type === "tool_event").length;
  const expertCount = run.events.filter((event) => isDelegateEvent(event)).length;

  return (
    <section className="agent-panel">
      <header>
        <h2>智能体过程</h2>
        <span className={`status-pill ${run.status}`}>{labelFor(run.status, statusLabels)}</span>
      </header>

      <CheckpointBanner run={run} />

      {processPanelV2Enabled ? (
        <>
          <ProcessOverview model={processModel} />
          <section className="process-chain-v2" data-testid="process-chain">
            <div className="process-chain-v2__header">
              <span className="label">执行链路</span>
              <span>{processModel.steps.length} 个业务步骤</span>
            </div>
            <ProcessTimeline steps={processModel.steps} currentStepId={processModel.currentStepId} />
          </section>
        </>
      ) : (
        <>
          <div className="panel-card process-overview">
            <span className="label">本轮概览</span>
            <div className="process-overview-grid">
              <div>
                <em>路由</em>
                <strong>{labelFor(run.route, routeLabels) || "待识别"}</strong>
              </div>
              <div>
                <em>模式</em>
                <strong>{labelFor(run.mode, modeLabels) || "自动"}</strong>
              </div>
              <div>
                <em>过程步数</em>
                <strong>{cards.length}</strong>
              </div>
              <div>
                <em>计划进度</em>
                <strong>{planItems.length > 0 ? `${donePlan}/${planItems.length}` : "—"}</strong>
              </div>
              <div>
                <em>工具调用</em>
                <strong>{toolCount}</strong>
              </div>
              <div>
                <em>专家委派</em>
                <strong>{expertCount}</strong>
              </div>
            </div>
          </div>

          <div className="panel-card" data-testid="process-chain">
            <span className="label">按步骤执行</span>
            {cards.length === 0 ? (
              <p>暂无过程事件</p>
            ) : (
              <ol className="timeline process-timeline">
                {cards.map((card) => (
                  <StepCardView key={card.id} card={card} />
                ))}
              </ol>
            )}
          </div>

          {run.answer ? (
            <div className="panel-card report-card">
              <span className="label">报告</span>
              <pre>{run.answer}</pre>
            </div>
          ) : null}
        </>
      )}

      {run.status === "completed" && run.answer ? (
        <>
          <SuggestedActionsCard run={run} onConfirmSuggestion={onConfirmSuggestion} />
          <EscalationCard run={run} />
          <FeedbackCard run={run} onFeedback={onFeedback} onDistill={onDistill} />
        </>
      ) : null}

      {run.error ? (
        <div className="panel-card error-card">
          <span className="label">错误</span>
          <p>{run.error}</p>
        </div>
      ) : null}
    </section>
  );
}
