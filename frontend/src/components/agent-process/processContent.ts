import type { TimelineEvent } from "../../types/events";

export type ProcessDetailField = {
  label: string;
  value: string;
};

export type ProcessDetailSection = {
  id: string;
  kind: "input" | "result" | "evidence" | "technical";
  title: "执行内容" | "关键结果" | "证据" | "技术信息";
  summary?: string;
  fields: ProcessDetailField[];
  items: string[];
  remaining?: number;
  raw?: string;
};

const ROUTE_LABELS: Record<string, string> = {
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
  clarify: "等待补充信息",
  unknown: "待识别",
  error: "识别失败",
};

const TOOL_LABELS: Record<string, string> = {
  retrieve_knowledge: "知识库检索",
  query_prometheus_alerts: "查询 Prometheus 告警",
  query_metrics: "查询指标",
  query_prometheus_range: "查询指标趋势",
  search_app_logs: "查询应用日志",
  query_logs: "查询日志",
  query_cls_logs: "查询 CLS 日志",
  query_recent_changes: "查询近期变更",
  lookup_service_knowledge: "查询服务知识",
  recall_experience: "召回历史经验",
  check_redis_health: "检查 Redis 健康状态",
  get_current_time: "获取当前时间",
  delegate_to_expert: "委派领域专家",
  delegate_parallel: "并行委派专家",
  context_read: "读取上下文",
  context_write: "更新上下文",
};

const AGENT_LABELS: Record<string, string> = {
  router: "路由分发",
  harness: "统一调度",
  planner: "计划生成",
  expert: "领域专家",
  knowledge: "知识问答专家",
  knowledge_expert: "知识问答专家",
  metric: "告警/指标专家",
  metric_expert: "告警/指标专家",
  log: "日志分析专家",
  log_expert: "日志分析专家",
  change: "变更/发布专家",
  change_expert: "变更/发布专家",
  diagnosis: "综合诊断专家",
};

const FIELD_LABELS: Record<string, string> = {
  keyword: "关键词",
  level: "日志级别",
  limit: "条数上限",
  service: "服务",
  service_name: "服务",
  target: "目标",
  query: "查询条件",
  time_window: "时间窗口",
  window: "时间窗口",
  start: "开始时间",
  end: "结束时间",
  expert: "专家",
  subtask: "子任务",
  metric: "指标",
  metric_name: "指标",
  top_k: "返回数量",
  path: "路径",
  host: "主机",
  namespace: "命名空间",
  status: "状态",
  source: "来源",
  count: "数量",
  total: "总数",
  message: "说明",
  summary: "摘要",
};

const SENTENCE_LABELS: Array<[RegExp, string]> = [
  [/^created lightweight investigation plan\.?$/i, "已生成轻量排查计划。"],
  [/^created investigation plan\.?$/i, "已生成排查计划。"],
  [/^calling model to decide the next action\.?$/i, "正在判断下一步动作。"],
  [/^verifying final answer against evidence\.?$/i, "正在核对结论与证据。"],
  [/^evidence verification complete; emitting final report\.?$/i, "证据核对完成，正在生成结论。"],
  [/^unified harness main loop completed\.?$/i, "本轮诊断已完成。"],
];

function asRecord(value: unknown): Record<string, unknown> | null {
  const parsed = parseJson(value);
  return parsed && typeof parsed === "object" && !Array.isArray(parsed)
    ? (parsed as Record<string, unknown>)
    : null;
}

function parseJson(value: unknown, depth = 0): unknown {
  if (depth > 3 || typeof value !== "string") {
    return value;
  }
  const input = value.trim();
  if (!input || (input[0] !== "{" && input[0] !== "[")) {
    return value;
  }
  try {
    return parseJson(JSON.parse(input), depth + 1);
  } catch {
    return value;
  }
}

function text(value: unknown, max = 220): string {
  if (value === null || value === undefined || value === "") {
    return "";
  }
  if (typeof value === "boolean") {
    return value ? "是" : "否";
  }
  const rendered = humanize(String(value));
  return rendered.length > max ? `${rendered.slice(0, max)}…` : rendered;
}

function humanize(value: string): string {
  let result = value.trim();
  for (const [pattern, replacement] of SENTENCE_LABELS) {
    if (pattern.test(result)) {
      return replacement;
    }
  }
  const tokenLabels: Record<string, string> = {
    ...TOOL_LABELS,
    ...AGENT_LABELS,
    root_cause: "根因",
    required_evidence: "要求证据",
    evidence_insufficient: "证据不足",
    step_timeout: "步骤超时",
  };
  for (const [token, label] of Object.entries(tokenLabels)) {
    result = result.replace(new RegExp(`\\b${token}\\b`, "gi"), label);
  }
  return result.replace(/\s{2,}/g, " ").trim();
}

function formatValue(value: unknown): string {
  const parsed = parseJson(value);
  if (Array.isArray(parsed)) {
    return parsed.map((item) => text(item, 80)).filter(Boolean).join("、");
  }
  if (parsed && typeof parsed === "object") {
    return text(JSON.stringify(parsed), 180);
  }
  return text(parsed, 180);
}

function fieldsFromRecord(value: unknown, limit = 12): ProcessDetailField[] {
  const record = asRecord(value);
  if (!record) {
    return [];
  }
  return Object.entries(record)
    .slice(0, limit)
    .map(([key, raw]) => ({
      label: FIELD_LABELS[key] ?? key.split("_").join(" "),
      value: key === "expert" ? presentAgent(String(raw)) : formatValue(raw),
    }))
    .filter((field) => Boolean(field.value));
}

function arrayItems(value: unknown): string[] {
  return Array.isArray(value)
    ? value
        .map((item) => {
          const row = asRecord(item);
          if (!row) {
            return text(item, 180);
          }
          return text(
            row.summary ?? row.message ?? row.alertname ?? row.content ?? row.symptom ?? row.answer,
            180,
          );
        })
        .filter(Boolean)
    : [];
}

type RecalledExperience = {
  id: string;
  antiPattern: boolean;
  confidence?: string;
  similarity?: string;
  symptoms?: string;
  rootCause?: string;
  resolution?: string;
  evidence?: string;
};

const RECALL_FIELD_NAMES: Record<string, keyof Omit<RecalledExperience, "id" | "antiPattern">> = {
  confidence: "confidence",
  similarity: "similarity",
  symptoms: "symptoms",
  verified_root_cause: "rootCause",
  effective_resolution: "resolution",
  evidence_summary: "evidence",
  dead_path: "rootCause",
  guidance: "resolution",
};

const RECALL_BROKEN_MARKER = /(?:鈿|璇佹嵁|[\uE000-\uF8FF])/;
const RECALL_PROTOCOL_MARKER = /(?:to=(?:multi_tool_use|functions\.)|"(?:tool_uses|recipient_name)")/i;

function cleanRecallValue(value: string): string {
  const cleaned = value
    .replace(/^(?:>\s*)+/, "")
    .replace(/^(?:(?:[-*+#])\s+)+/, "")
    .trim();
  return RECALL_BROKEN_MARKER.test(cleaned) || RECALL_PROTOCOL_MARKER.test(cleaned) ? "" : cleaned;
}

function recallScore(value?: string): string | undefined {
  if (!value) {
    return undefined;
  }
  const score = Number(value);
  return Number.isFinite(score) ? score.toFixed(2) : value;
}

function recallExperiencePresentation(value: unknown): {
  summary: string;
  fields: ProcessDetailField[];
  items: string[];
} | null {
  if (typeof value !== "string") {
    return null;
  }
  if (value.trim() === "未命中可复用的历史诊断经验。") {
    return { summary: "未命中可复用的历史诊断经验。", fields: [], items: [] };
  }

  const experiences: RecalledExperience[] = [];
  let current: RecalledExperience | null = null;
  for (const rawLine of value.split(/\r?\n/)) {
    const line = rawLine.trim();
    const idMatch = line.match(
      /^(?:-\s*)?(\[反模式\/勿重复\]\s*)?experience_id:\s*(.+)$/i,
    );
    if (idMatch) {
      const id = cleanRecallValue(idMatch[2]);
      if (id) {
        current = { id, antiPattern: Boolean(idMatch[1]) };
        experiences.push(current);
      }
      continue;
    }
    if (!current) {
      continue;
    }
    const fieldMatch = line.match(
      /^(confidence|similarity|symptoms|verified_root_cause|effective_resolution|evidence_summary|dead_path|guidance):\s*(.*)$/i,
    );
    if (!fieldMatch) {
      continue;
    }
    const key = RECALL_FIELD_NAMES[fieldMatch[1].toLowerCase()];
    const cleaned = cleanRecallValue(fieldMatch[2]);
    if (key && cleaned) {
      current[key] = cleaned;
    }
  }

  if (experiences.length === 0) {
    return null;
  }

  const fields: ProcessDetailField[] = [];
  const items: string[] = [];
  for (const [index, experience] of experiences.slice(0, 3).entries()) {
    const suffix = index === 0 ? "" : ` ${index + 1}`;
    fields.push({ label: `经验 ID${suffix}`, value: experience.id });
    const confidence = recallScore(experience.confidence);
    const similarity = recallScore(experience.similarity);
    if (confidence) {
      fields.push({ label: `置信度${suffix}`, value: confidence });
    }
    if (similarity) {
      fields.push({ label: `相似度${suffix}`, value: similarity });
    }
    if (experience.antiPattern) {
      items.push(`反模式${suffix}：勿重复此历史路径`);
    }
    if (experience.symptoms) {
      items.push(`症状${suffix}：${experience.symptoms}`);
    }
    if (experience.rootCause) {
      items.push(`${experience.antiPattern ? "历史无效路径" : "历史根因"}${suffix}：${experience.rootCause}`);
    }
    if (experience.resolution) {
      items.push(`${experience.antiPattern ? "改进建议" : "处置建议"}${suffix}：${experience.resolution}`);
    }
    if (experience.evidence) {
      items.push(`证据摘要${suffix}：${experience.evidence}`);
    }
  }

  return {
    summary: `召回 ${experiences.length} 条历史经验，仅供参考，需以当前证据复核。`,
    fields,
    items,
  };
}

function toolResultPresentation(event: TimelineEvent): {
  summary: string;
  fields: ProcessDetailField[];
  items: string[];
  raw?: string;
} {
  const payload = event.payload ?? {};
  if (event.tool === "recall_experience") {
    const recalled = recallExperiencePresentation(payload.result);
    if (recalled) {
      return recalled;
    }
  }
  const result = parseJson(payload.result);
  const data = asRecord(result);
  if (!data) {
    return { summary: text(result), fields: [], items: [] };
  }

  if (event.tool === "context_read") {
    const body = asRecord(data.data) ?? data;
    const fields: ProcessDetailField[] = [];
    const items: string[] = [];
    const sectionLabels: Record<string, string> = {
      evidence: "证据",
      intent: "意图",
      working: "工作计划",
      conversation: "会话",
      output: "输出约定",
    };
    const section = text(data.section);
    if (section) {
      fields.push({ label: "分区", value: sectionLabels[section] ?? section });
    }
    const summaries = Array.isArray(body.tool_summaries) ? body.tool_summaries : [];
    if (summaries.length > 0) {
      fields.push({ label: "工具摘要", value: `${summaries.length} 条` });
      for (const item of summaries) {
        const row = asRecord(item);
        if (!row) {
          continue;
        }
        const note = asRecord(row.note);
        const parts = [
          presentTool(String(row.tool ?? "")),
          text(row.status),
          note?.expert ? `专家 ${presentAgent(String(note.expert))}` : "",
          note?.subtask ? `任务 ${text(note.subtask, 140)}` : "",
          note?.answer ? `结论 ${text(note.answer, 140)}` : "",
        ].filter(Boolean);
        items.push(parts.join(" · "));
      }
    }
    const facts = Array.isArray(body.observed_facts) ? body.observed_facts : [];
    if (facts.length > 0) {
      fields.push({ label: "已观察事实", value: `${facts.length} 条` });
      for (const fact of facts) {
        const row = asRecord(fact);
        items.push(
          row
            ? [presentTool(String(row.source ?? row.tool ?? "")), text(row.status), formatValue(row.facts)]
                .filter(Boolean)
                .join(" · ")
            : text(fact, 180),
        );
      }
    }
    return {
      summary: fields.map((field) => `${field.label}：${field.value}`).join(" · ") || "已读取上下文",
      fields,
      items: items.filter(Boolean),
    };
  }

  if (event.tool === "delegate_parallel" || data.parallel === true) {
    const results = Array.isArray(data.results) ? data.results : [];
    const experts = Array.isArray(data.experts)
      ? data.experts.map((item) => presentAgent(String(item))).filter(Boolean)
      : results
          .map((item) => {
            const row = asRecord(item);
            return row ? presentAgent(String(row.expert ?? "")) : "";
          })
          .filter(Boolean);
    const fields: ProcessDetailField[] = [];
    if (data.status !== undefined) {
      fields.push({ label: "状态", value: text(data.status) });
    }
    if (experts.length > 0) {
      fields.push({ label: "专家", value: experts.join("、") });
    }
    if (data.wall_ms !== undefined) {
      fields.push({ label: "耗时", value: `${text(data.wall_ms)} ms` });
    }
    const items = results
      .map((item) => {
        const row = asRecord(item);
        if (!row) {
          return text(item, 180);
        }
        return [
          presentAgent(String(row.expert ?? "")),
          text(row.status),
          text(row.answer ?? row.error ?? row.subtask, 120),
        ]
          .filter(Boolean)
          .join(" · ");
      })
      .filter(Boolean);
    return {
      summary:
        experts.length > 0
          ? `并行委派 ${experts.join("、")}${data.wall_ms !== undefined ? ` · ${text(data.wall_ms)} ms` : ""}`
          : text(data.summary ?? data.message) || "并行委派专家",
      fields,
      items,
    };
  }

  if (event.tool === "delegate_to_expert" || data.expert) {
    const fields: ProcessDetailField[] = [];
    if (data.status !== undefined) {
      fields.push({ label: "状态", value: text(data.status) });
    }
    const answer = text(data.answer ?? data.summary ?? data.message, 240);
    if (answer) {
      fields.push({ label: "结论", value: answer });
    }
    return {
      summary: answer || fields.map((field) => `${field.label}：${field.value}`).join(" · "),
      fields,
      items: [],
    };
  }

  const fields: ProcessDetailField[] = [];
  for (const key of ["status", "source", "count", "total", "service", "service_name", "message", "summary"]) {
    if (data[key] !== undefined) {
      const value = formatValue(data[key]);
      if (value) {
        fields.push({ label: FIELD_LABELS[key] ?? key, value });
      }
    }
  }

  let items: string[] = [];
  for (const key of ["logs", "alerts", "changes", "items", "results", "experiences", "observed_facts"]) {
    items = arrayItems(data[key]);
    if (items.length > 0) {
      break;
    }
  }
  if (items.length === 0 && data.answer) {
    items = [text(data.answer, 240)];
  }

  const summary = text(data.answer ?? data.summary ?? data.message, 240);
  return {
    summary: summary || fields.map((field) => `${field.label}：${field.value}`).join(" · "),
    fields,
    items,
    raw: fields.length === 0 && items.length === 0 ? JSON.stringify(data, null, 2) : undefined,
  };
}

export function limitItems(items: string[], limit = 6) {
  const visible = items.slice(0, limit);
  return { visible, remaining: Math.max(0, items.length - visible.length) };
}

export function presentRoute(route?: string): string {
  return route ? ROUTE_LABELS[route] ?? route : "待识别";
}

export function presentTool(tool?: string): string {
  return tool ? TOOL_LABELS[tool] ?? tool.split("_").join(" ") : "工具调用";
}

export function presentAgent(agent?: string): string {
  return agent ? AGENT_LABELS[agent] ?? AGENT_LABELS[`${agent}_expert`] ?? agent : "智能体";
}

export function presentEventTitle(event: TimelineEvent): string {
  if (event.type === "route_event") {
    return "识别请求";
  }
  if (event.type === "tool_event") {
    return presentTool(event.tool);
  }
  if (event.stage === "plan" || event.stage === "planning") {
    return "制定排查计划";
  }
  if (event.stage === "verify") {
    return "证据自检";
  }
  if (event.stage === "report" || event.stage === "complete") {
    return "生成结论";
  }
  return presentAgent(event.agent);
}

export function presentEventSummary(event: TimelineEvent): string {
  if (event.type === "route_event") {
    return `路由为${presentRoute(event.route)}`;
  }
  if (event.type === "tool_event") {
    const result = toolResultPresentation(event);
    if (
      event.tool === "context_read" ||
      event.tool === "delegate_to_expert" ||
      event.tool === "delegate_parallel"
    ) {
      return result.summary || humanize(event.summary ?? "") || `${presentTool(event.tool)}已执行`;
    }
    return humanize(event.summary ?? "") || result.summary || `${presentTool(event.tool)}已执行`;
  }
  if (event.stage === "delegate_parallel_start" || event.stage === "delegate_parallel_done") {
    const experts = Array.isArray(event.payload?.experts)
      ? event.payload.experts.map((item) => presentAgent(String(item))).filter(Boolean)
      : [];
    if (experts.length > 0) {
      return event.stage === "delegate_parallel_done"
        ? `并行专家完成：${experts.join("、")}`
        : `并行委派 ${experts.join("、")}`;
    }
  }
  return humanize(event.summary ?? "");
}

export function presentEventDetails(event: TimelineEvent): ProcessDetailSection[] {
  const payload = event.payload ?? {};
  const sections: ProcessDetailSection[] = [];
  const inputFields = fieldsFromRecord(payload.arguments);
  if (payload.subtask) {
    inputFields.push({ label: "子任务", value: text(payload.subtask, 180) });
  }
  if (payload.reason) {
    inputFields.push({ label: "原因", value: text(payload.reason, 180) });
  }
  if (inputFields.length > 0) {
    sections.push({ id: "input", kind: "input", title: "执行内容", fields: inputFields, items: [] });
  }

  if (event.type === "tool_event" && payload.result !== undefined) {
    const result = toolResultPresentation(event);
    const limited = limitItems(result.items);
    if (result.summary || result.fields.length > 0 || limited.visible.length > 0 || result.raw) {
      sections.push({
        id: "result",
        kind: "result",
        title: "关键结果",
        summary: result.summary,
        fields: result.fields,
        items: limited.visible,
        remaining: limited.remaining,
        raw: result.raw,
      });
    }
  }

  const evidenceItems = [
    ...(Array.isArray(payload.required_evidence) ? payload.required_evidence.map((item) => text(item, 180)) : []),
    ...(Array.isArray(payload.gaps) ? payload.gaps.map((item) => text(item, 180)) : []),
  ].filter(Boolean);
  const evidenceFields: ProcessDetailField[] = [];
  if (payload.evidence_count !== undefined) {
    evidenceFields.push({ label: "成功证据", value: text(payload.evidence_count) });
  }
  if (payload.failed_evidence_count !== undefined) {
    evidenceFields.push({ label: "失败证据", value: text(payload.failed_evidence_count) });
  }
  if (payload.confidence !== undefined) {
    evidenceFields.push({ label: "置信度", value: text(payload.confidence) });
  }
  if (evidenceFields.length > 0 || evidenceItems.length > 0) {
    const limited = limitItems(evidenceItems);
    sections.push({
      id: "evidence",
      kind: "evidence",
      title: "证据",
      fields: evidenceFields,
      items: limited.visible,
      remaining: limited.remaining,
    });
  }

  const technicalFields: ProcessDetailField[] = [];
  if (event.evidence_id) {
    technicalFields.push({ label: "Evidence ID", value: event.evidence_id });
  }
  if (event.trace_id) {
    technicalFields.push({ label: "Trace ID", value: event.trace_id });
  }
  if (event.span_id) {
    technicalFields.push({ label: "Span ID", value: event.span_id });
  }
  if (event.duration_ms !== undefined) {
    technicalFields.push({ label: "耗时", value: `${Math.round(event.duration_ms)} ms` });
  }
  if (event.usage && Object.keys(event.usage).length > 0) {
    technicalFields.push({ label: "Token usage", value: JSON.stringify(event.usage) });
  }
  if (technicalFields.length > 0) {
    sections.push({ id: "technical", kind: "technical", title: "技术信息", fields: technicalFields, items: [] });
  }

  return sections;
}
