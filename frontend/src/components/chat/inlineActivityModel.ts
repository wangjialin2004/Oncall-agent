import type { AgentRun, TimelineEvent } from "../../types/events";

export type InlineActivityState =
  | "queued"
  | "running"
  | "completed"
  | "degraded"
  | "failed";

export type InlineActivityDetail = {
  label: string;
  value: string;
};

export type InlineActivityItem = {
  id: string;
  kind: "route" | "plan" | "tool" | "expert-group" | "expert" | "verify" | "report";
  label: string;
  state: InlineActivityState;
  durationMs?: number;
  children?: InlineActivityItem[];
  parentId?: string;
  depth?: 0 | 1 | 2;
  description?: string;
  details?: InlineActivityDetail[];
  publicDetails?: InlineActivityDetail[];
};

export type InlineActivityModel = {
  items: InlineActivityItem[];
  feed: InlineActivityItem[];
  summary: string;
  toolCount: number;
  expertCount: number;
  completedCount: number;
  totalCount: number;
};

const ROUTE_LABELS: Record<string, string> = {
  knowledge: "知识问答",
  metric: "告警指标",
  log: "日志分析",
  change: "变更发布",
  diagnosis: "综合诊断",
  clarify: "信息确认",
};

const TOOL_LABELS: Record<string, string> = {
  retrieve_knowledge: "检索知识库",
  query_prometheus_alerts: "查询告警",
  query_metrics: "查询指标",
  query_cpu_metrics: "查询 CPU 指标",
  query_prometheus_range: "查询指标趋势",
  search_app_logs: "检索应用日志",
  query_logs: "检索日志",
  query_cls_logs: "检索 CLS 日志",
  query_recent_changes: "查询近期变更",
  lookup_service_knowledge: "查询服务知识",
  recall_experience: "召回历史经验",
  check_redis_health: "检查 Redis 状态",
  get_current_time: "获取当前时间",
  read_attachment: "读取附件",
  context_read: "读取上下文",
  context_write: "更新上下文",
};

const EXPERT_LABELS: Record<string, string> = {
  knowledge: "知识专家",
  metric: "指标专家",
  log: "日志专家",
  change: "变更专家",
  diagnosis: "诊断专家",
};

const KIND_LABELS: Record<InlineActivityItem["kind"], string> = {
  route: "路由节点",
  plan: "计划节点",
  tool: "工具调用",
  "expert-group": "Agent 调度",
  expert: "Agent",
  verify: "证据核对",
  report: "报告节点",
};

const TOOL_DESCRIPTIONS: Record<string, string> = {
  查询告警: "读取当前告警状态，补充排查所需的只读信息。",
  查询指标: "读取异常时段的指标数据，用于识别趋势和相关性。",
  "查询 CPU 指标": "读取异常时段的 CPU 指标，用于区分负载类型。",
  查询指标趋势: "读取一段时间内的指标变化，用于定位异常起点。",
  检索应用日志: "检索应用日志中的异常模式和关键时间点。",
  检索日志: "检索日志中的异常模式和关键时间点。",
  "检索 CLS 日志": "检索日志服务中的异常模式和关键时间点。",
  查询近期变更: "核对异常时段附近的发布、配置和依赖变更。",
  查询服务知识: "读取已审核的服务知识和正常基线。",
  检索知识库: "检索已审核的运维知识与处理手册。",
  召回历史经验: "召回相似历史案例，作为当前证据的补充参考。",
  "检查 Redis 状态": "检查 Redis 的连接与运行状态。",
  获取当前时间: "读取当前时间，用于统一排查时间范围。",
  读取附件: "读取本轮已上传附件中的可用内容。",
  读取上下文: "读取本轮排查已经确认的上下文。",
  更新上下文: "更新本轮排查的结构化上下文。",
  执行检查: "执行当前节点需要的只读检查。",
};

const RESULT_FIELD_LABELS: Record<string, string> = {
  status: "结果状态",
  success: "执行成功",
  source: "数据来源",
  metric_name: "指标名称",
  retrieval_type: "取数方式",
  interval: "采样间隔",
  count: "结果数量",
  total: "结果总数",
  series_count: "序列数量",
  data_points_count: "数据点数量",
  duration_ms: "工具耗时（毫秒）",
  service: "服务",
  service_name: "服务",
  message: "结果说明",
  summary: "结果摘要",
  answer: "结果结论",
  error: "错误说明",
  error_code: "错误代码",
  source_available: "数据源可用",
  capability_available: "能力可用",
  gap: "缺口",
  warning: "注意事项",
  note: "补充说明",
  "statistics.avg": "平均值",
  "statistics.max": "最大值",
  "statistics.min": "最小值",
  "statistics.p95": "P95",
  "statistics.threshold_exceeded": "超过阈值",
  "statistics.memory_pressure": "内存压力",
  "alert_info.triggered": "告警触发",
  "alert_info.threshold": "告警阈值",
  "alert_info.message": "告警说明",
  "cpu.usage_percent": "CPU 使用率",
  "cpu.count": "CPU 核心数",
  "memory.usage_percent": "内存使用率",
  "memory.total_bytes": "内存总量（字节）",
  "memory.used_bytes": "已用内存（字节）",
  "memory.available_bytes": "可用内存（字节）",
  "disk.usage_percent": "磁盘使用率",
  "disk.total_bytes": "磁盘总量（字节）",
  "disk.used_bytes": "已用磁盘（字节）",
  "disk.free_bytes": "可用磁盘（字节）",
};

const SECRET_PATTERN = /\b(token|api[_-]?key|secret|password|authorization|cookie|access[_-]?key)\b\s*[:=]\s*([^\s,;]+)/gi;
const EMAIL_PATTERN = /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g;
const PHONE_PATTERN = /\b1[3-9]\d{9}\b/g;
const NUMERIC_RESULT_FIELD_LABELS = new Set([
  "count",
  "total",
  "series_count",
  "data_points_count",
  "duration_ms",
  "statistics.avg",
  "statistics.max",
  "statistics.min",
  "statistics.p95",
  "alert_info.threshold",
  "cpu.usage_percent",
  "cpu.count",
  "memory.usage_percent",
  "memory.total_bytes",
  "memory.used_bytes",
  "memory.available_bytes",
  "disk.usage_percent",
  "disk.total_bytes",
  "disk.used_bytes",
  "disk.free_bytes",
]);

function publicRoute(route: unknown): string {
  const key = safeString(route);
  return ROUTE_LABELS[key] ?? "待识别";
}

function publicTool(tool: unknown): string {
  const key = safeString(tool);
  return TOOL_LABELS[key] ?? "执行检查";
}

function publicExpert(expert: unknown): string {
  return EXPERT_LABELS[expertKey(expert)] ?? "领域专家";
}

function payloadOf(event: TimelineEvent): Record<string, unknown> {
  return event.payload && typeof event.payload === "object" ? event.payload : {};
}

function safeString(value: unknown): string {
  return typeof value === "string" ? value.slice(0, 160) : "";
}

function safeDetailText(value: unknown, limit = 280): string {
  if (typeof value !== "string") return "";
  return value
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, " ")
    .replace(SECRET_PATTERN, (_whole, name: string) => `${name}=[REDACTED]`)
    .replace(EMAIL_PATTERN, "[REDACTED_EMAIL]")
    .replace(PHONE_PATTERN, "[REDACTED_PHONE]")
    .trim()
    .slice(0, limit);
}

function safeDetailList(value: unknown, limit = 8): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .slice(0, limit)
    .map((item) => safeDetailText(item))
    .filter(Boolean);
}

function safeResultFieldValue(label: string, value: unknown): string {
  if (NUMERIC_RESULT_FIELD_LABELS.has(label) && typeof value === "string") {
    const numeric = value.trim().slice(0, 280);
    if (/^-?(?:\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?$/i.test(numeric)) {
      return numeric;
    }
  }
  return safeDetailText(value);
}

function publicDetailsForEvent(
  event: TimelineEvent,
  kind: InlineActivityItem["kind"],
): InlineActivityDetail[] {
  const payload = payloadOf(event);
  const details: InlineActivityDetail[] = [];
  const addList = (label: string, value: unknown, prefix = "") => {
    const entries = safeDetailList(value);
    if (entries.length) {
      details.push({ label, value: entries.map((entry) => `${prefix}${entry}`).join("；") });
    }
  };

  if (kind === "plan") {
    const todos = safeDetailList(payload.todos);
    if (todos.length) {
      details.push({
        label: "计划步骤",
        value: todos.map((todo, index) => `${index + 1}. ${todo}`).join("；"),
      });
    }
    addList("所需证据", payload.required_evidence);
    addList("当前缺口", payload.gaps);
    addList("失败工具", payload.failed_tools);
    const trigger = safeDetailText(payload.trigger, 160);
    if (trigger) details.push({ label: "调整原因", value: trigger });
  }

  if (kind === "tool") {
    const preview = safeDetailText(payload.result_preview, 640);
    if (preview) details.push({ label: "结果摘要", value: preview });
    if (Array.isArray(payload.result_fields)) {
      for (const item of payload.result_fields.slice(0, 12)) {
        if (!item || typeof item !== "object") continue;
        const field = item as Record<string, unknown>;
        const key = safeDetailText(field.label, 60);
        const value = safeResultFieldValue(key, field.value);
        if (key && value) {
          details.push({ label: RESULT_FIELD_LABELS[key] ?? "结果字段", value });
        }
      }
    }
    const resultItems = safeDetailList(payload.result_items, 6);
    if (resultItems.length) {
      details.push({ label: "结果条目", value: resultItems.join("；") });
    }
  }

  if (kind === "verify") {
    if (typeof payload.evidence_count === "number") {
      details.push({ label: "成功证据", value: `${payload.evidence_count}` });
    }
    if (typeof payload.failed_evidence_count === "number") {
      details.push({ label: "失败或降级工具", value: `${payload.failed_evidence_count}` });
    }
    const confidence = safeDetailText(payload.confidence, 40);
    if (confidence) details.push({ label: "置信度", value: confidence });
    addList("缺少证据", payload.gaps);
  }

  return details;
}

function safeDuration(event: TimelineEvent): number | undefined {
  return typeof event.duration_ms === "number" && Number.isFinite(event.duration_ms)
    ? Math.max(0, event.duration_ms)
    : undefined;
}

function stateOf(status: unknown): InlineActivityState {
  const normalized = safeString(status).toLowerCase();
  if (["in_progress", "running", "started"].includes(normalized)) return "running";
  if (["queued", "pending", "waiting"].includes(normalized)) return "queued";
  if (["failed", "error"].includes(normalized)) return "failed";
  if (["timeout", "timed_out", "degraded"].includes(normalized)) {
    return "degraded";
  }
  return "completed";
}

function activityId(event: TimelineEvent): string {
  return safeString(payloadOf(event).tool_call_id);
}

function parentActivityId(event: TimelineEvent): string {
  return safeString(payloadOf(event).parent_tool_call_id);
}

function expertKey(value: unknown): string {
  return safeString(value).replace(/_expert$/, "") || "expert";
}

function presentState(state: InlineActivityState): string {
  if (state === "running") return "进行中";
  if (state === "queued") return "等待中";
  if (state === "degraded") return "已降级继续";
  if (state === "failed") return "失败";
  return "已完成";
}

function presentDuration(durationMs: number): string {
  if (durationMs < 1000) return `${Math.round(durationMs)} 毫秒`;
  return `${(durationMs / 1000).toFixed(1)} 秒`;
}

function activityDescription(item: InlineActivityItem): string {
  if (item.kind === "tool") {
    return TOOL_DESCRIPTIONS[item.label] ?? TOOL_DESCRIPTIONS.执行检查;
  }
  if (item.kind === "route") return "判断问题类型，并选择本轮处理路径。";
  if (item.kind === "plan") return "生成或调整本轮排查步骤。";
  if (item.kind === "expert-group") return "协调领域 Agent 并行补充不同方向的证据。";
  if (item.kind === "expert") return `${item.label}正在处理分配到的证据任务。`;
  if (item.kind === "verify") return "检查当前证据是否足以支撑结论。";
  return "整理已确认的证据并生成最终回答。";
}

export function buildInlineActivityModel(run: AgentRun): InlineActivityModel {
  const items: InlineActivityItem[] = [];
  const byId = new Map<string, InlineActivityItem>();
  const feedOrder: string[] = [];
  const feedById = new Map<string, InlineActivityItem>();

  const registerFeed = (
    item: InlineActivityItem,
    parentId?: string,
    depth: 0 | 1 | 2 = 0,
  ) => {
    if (!feedById.has(item.id)) {
      item.parentId = parentId;
      item.depth = depth;
      feedById.set(item.id, item);
      feedOrder.push(item.id);
    }
    return item;
  };

  const upsert = (item: InlineActivityItem) => {
    const current = byId.get(item.id);
    if (current) {
      current.label = item.label || current.label;
      current.state = item.state;
      current.durationMs = item.durationMs ?? current.durationMs;
      if (item.children) current.children = item.children;
      if (item.publicDetails?.length) current.publicDetails = item.publicDetails;
      return current;
    }
    byId.set(item.id, item);
    items.push(item);
    return registerFeed(item);
  };

  const expertGroup = (id: string, state: InlineActivityState = "running") =>
    upsert({
      id: `experts:${id || "current"}`,
      kind: "expert-group",
      label: "调用领域专家",
      state,
      children: byId.get(`experts:${id || "current"}`)?.children ?? [],
    });

  const upsertExpert = (
    group: InlineActivityItem,
    expertValue: unknown,
    state: InlineActivityState,
  ) => {
    const expert = expertKey(expertValue);
    const id = `${group.id}:${expert}`;
    const current = group.children?.find((item) => item.id === id);
    if (current) {
      current.state = state;
      return current;
    }
    const child: InlineActivityItem = {
      id,
      kind: "expert",
      label: publicExpert(expert),
      state,
    };
    group.children = [...(group.children ?? []), child];
    return registerFeed(child, group.id, 1);
  };

  const updateExpertGroupState = (group: InlineActivityItem) => {
    const children = group.children ?? [];
    const terminalStates: InlineActivityState[] = ["completed", "degraded", "failed"];
    if (children.length > 0 && children.every((child) => terminalStates.includes(child.state))) {
      group.state = children.some((child) => child.state === "failed")
        ? "failed"
        : children.some((child) => child.state === "degraded")
          ? "degraded"
          : "completed";
    }
  };

  const upsertExpertTool = (
    group: InlineActivityItem,
    expertValue: unknown,
    event: TimelineEvent,
    payload: Record<string, unknown>,
    state: InlineActivityState,
  ) => {
    const expertId = `${group.id}:${expertKey(expertValue)}`;
    const expert =
      group.children?.find((item) => item.id === expertId) ??
      upsertExpert(group, expertValue, "running");
    const id = activityId(event) || `${expert.id}:tool-${expert.children?.length ?? 0}`;
    const tool: InlineActivityItem = {
      id: `tool:${id}`,
      kind: "tool",
      label: publicTool(payload.tool ?? event.tool),
      state,
      durationMs: safeDuration(event),
      publicDetails: publicDetailsForEvent(event, "tool"),
    };
    const current = expert.children?.find((item) => item.id === tool.id);
    if (current) {
      current.label = tool.label || current.label;
      current.state = tool.state;
      current.durationMs = tool.durationMs ?? current.durationMs;
      if (tool.publicDetails?.length) current.publicDetails = tool.publicDetails;
      return current;
    }
    expert.children = [...(expert.children ?? []), tool];
    return registerFeed(tool, expert.id, 2);
  };

  for (const event of run.events) {
    const payload = payloadOf(event);
    const stage = safeString(event.stage);
    const eventState = stateOf(event.status);

    if (event.type === "route_event") {
      upsert({
        id: "route",
        kind: "route",
        label: `已识别为${publicRoute(event.route)}`,
        state: eventState,
        durationMs: safeDuration(event),
      });
      continue;
    }

    if (["plan", "planning", "replan"].includes(stage)) {
      upsert({
        id: stage === "replan" ? "replan" : "plan",
        kind: "plan",
        label: stage === "replan" ? "正在调整排查计划" : "已制定排查计划",
        state: eventState,
        durationMs: safeDuration(event),
        publicDetails: publicDetailsForEvent(event, "plan"),
      });
      continue;
    }

    const parentId = parentActivityId(event);

    if (stage === "delegate_parallel_done") {
      const group = expertGroup(activityId(event) || parentId, eventState);
      if (Array.isArray(payload.results)) {
        for (const item of payload.results) {
          if (!item || typeof item !== "object") continue;
          const row = item as Record<string, unknown>;
          upsertExpert(group, row.expert, stateOf(row.status));
        }
      } else {
        for (const child of group.children ?? []) child.state = eventState;
      }
      group.durationMs = safeDuration(event);
      continue;
    }

    if (parentId) {
      const group = expertGroup(parentId);
      const expertValue = payload.delegated_expert ?? event.agent;
      if (stage === "tool_start" || event.type === "tool_event") {
        upsertExpertTool(group, expertValue, event, payload, stage === "tool_start" ? "running" : eventState);
      } else {
        upsertExpert(group, expertValue, eventState);
      }
      updateExpertGroupState(group);
      continue;
    }

    if (stage === "tool_start") {
      const id = activityId(event) || `event-${items.length + 1}`;
      upsert({
        id: `tool:${id}`,
        kind: "tool",
        label: publicTool(payload.tool),
        state: "running",
        durationMs: safeDuration(event),
        publicDetails: publicDetailsForEvent(event, "tool"),
      });
      continue;
    }

    if (stage === "delegate_parallel_start" || stage === "delegate_start") {
      const group = expertGroup(activityId(event), "running");
      const experts = Array.isArray(payload.experts)
        ? payload.experts
        : [payload.delegated_expert];
      experts.forEach((expert) => upsertExpert(group, expert, "running"));
      group.label = (group.children?.length ?? 0) > 1
        ? `并行调用 ${group.children?.length ?? 0} 位专家`
        : "调用领域专家";
      continue;
    }

    if (event.type === "tool_event") {
      const id = activityId(event) || `event-${items.length + 1}`;
      if (event.tool === "delegate_parallel" || event.tool === "delegate_to_expert") {
        expertGroup(id, eventState);
      } else {
        upsert({
          id: `tool:${id}`,
          kind: "tool",
          label: publicTool(event.tool),
          state: eventState,
          durationMs: safeDuration(event),
          publicDetails: publicDetailsForEvent(event, "tool"),
        });
      }
      continue;
    }

    if (["verify", "re_evidence"].includes(stage)) {
      upsert({
        id: stage,
        kind: "verify",
        label: stage === "re_evidence" ? "正在补充证据" : "正在核对证据",
        state: eventState,
        durationMs: safeDuration(event),
        publicDetails: publicDetailsForEvent(event, "verify"),
      });
      continue;
    }

    if (["report", "model_closing", "fallback_start", "complete"].includes(stage)) {
      upsert({
        id: "report",
        kind: "report",
        label: "正在整理结论",
        state: eventState,
        durationMs: safeDuration(event),
      });
    }
  }

  if (items.length === 0 && run.status === "running") {
    upsert({ id: "prepare", kind: "plan", label: "正在准备排查", state: "running" });
  }

  const flatten = (source: InlineActivityItem[]): InlineActivityItem[] =>
    source.flatMap((item) => [item, ...flatten(item.children ?? [])]);
  const flatItems = flatten(items);
  const expertCount = flatItems.filter((item) => item.kind === "expert").length;
  const toolCount = flatItems.filter((item) => item.kind === "tool").length;
  const totalCount = flatItems.length;
  const completedCount = flatItems.filter(
    (item) => ["completed", "degraded", "failed"].includes(item.state),
  ).length;
  const summaryLead =
    run.status === "running"
      ? "正在排查"
      : run.status === "error"
        ? "执行失败"
        : run.status === "cancelled"
          ? "已停止"
          : "已完成";
  const summaryParts = [summaryLead];
  if (items.length) summaryParts.push(`${items.length} 步`);
  if (expertCount) summaryParts.push(`${expertCount} 位专家`);
  if (toolCount) summaryParts.push(`${toolCount} 次工具调用`);

  const feed = feedOrder.flatMap((id) => {
    const item = feedById.get(id);
    if (!item) return [];
    const parent = item.parentId ? feedById.get(item.parentId) : undefined;
    const details: InlineActivityDetail[] = [
      { label: "节点类型", value: KIND_LABELS[item.kind] },
      { label: "执行状态", value: presentState(item.state) },
    ];
    if (item.durationMs !== undefined) {
      details.push({ label: "耗时", value: presentDuration(item.durationMs) });
    }
    if (parent?.kind === "expert") {
      details.push({ label: "所属 Agent", value: parent.label });
    } else if (parent?.kind === "expert-group") {
      details.push({ label: "所属调度", value: parent.label });
    }
    if (item.kind === "expert-group" && item.children?.length) {
      details.push({ label: "Agent 数量", value: `${item.children.length} 位` });
    }
    details.push(...(item.publicDetails ?? []));
    return [{
      ...item,
      children: undefined,
      description: activityDescription(item),
      details,
    }];
  });

  return {
    items,
    feed,
    summary: summaryParts.join(" · "),
    toolCount,
    expertCount,
    completedCount,
    totalCount,
  };
}
