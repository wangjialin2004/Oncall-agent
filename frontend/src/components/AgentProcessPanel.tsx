import { useState } from "react";
import { Activity, CheckCircle2, CircleAlert, GitBranch, Wrench } from "lucide-react";

import type { AgentRun, TimelineEvent } from "../types/events";

type FeedbackHandler = (kind: "adopted" | "corrected", actualRootCause?: string) => void;

type AgentProcessPanelProps = {
  run: AgentRun;
  onFeedback?: FeedbackHandler;
};

const statusLabels: Record<string, string> = {
  idle: "待命",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  degraded: "降级",
  error: "错误",
  cancelled: "已取消",
  evidence_insufficient: "证据不足",
  root_cause_ready: "根因已就绪",
};

const routeLabels: Record<string, string> = {
  knowledge: "知识问答",
  metric: "告警/指标",
  log: "日志分析",
  change: "变更/发布",
  diagnosis: "综合诊断",
  clarify: "待澄清",
  unknown: "未知",
  error: "错误",
};

const modeLabels: Record<string, string> = {
  auto: "自动",
  rag: "知识库",
};

const agentLabels: Record<string, string> = {
  router: "路由分发",
  harness: "统一 Harness",
  knowledge_expert: "知识问答专家",
  metric_expert: "告警/指标专家",
  log_expert: "日志分析专家",
  change_expert: "变更/发布专家",
  diagnosis: "综合诊断专家",
};

const stageLabels: Record<string, string> = {
  route: "路由识别",
  context: "上下文准备",
  planning: "计划生成",
  model_decision: "模型决策",
  model_closing: "模型收尾",
  report: "报告输出",
  start: "开始",
  plan: "规划",
  verify: "证据自检",
  budget: "预算控制",
  no_progress: "无进展检测",
  complete: "完成",
  error: "出错",
  log_pipeline: "日志预处理",
  log_mapreduce: "日志摘要",
  clarify_missing_params: "补充参数",
  timeout_fallback: "超时降级",
  delegate_start: "专家委派",
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
  return (
    event.stage === "delegate_start" ||
    event.tool === "delegate_to_expert" ||
    Boolean(expertLabelFromEvent(event))
  );
}

function eventIcon(event: TimelineEvent) {
  if (isDelegateEvent(event)) {
    return <GitBranch size={16} aria-hidden="true" />;
  }
  if (event.type === "route_event") {
    return <GitBranch size={16} aria-hidden="true" />;
  }
  if (event.type === "tool_event") {
    return <Wrench size={16} aria-hidden="true" />;
  }
  if (event.status === "completed") {
    return <CheckCircle2 size={16} aria-hidden="true" />;
  }
  if (event.status === "failed" || event.status === "degraded") {
    return <CircleAlert size={16} aria-hidden="true" />;
  }
  return <Activity size={16} aria-hidden="true" />;
}

function eventTitle(event: TimelineEvent) {
  if (event.type === "route_event") {
    return "路由分发";
  }
  if (event.stage === "delegate_start") {
    return `进入专家：${expertLabelFromEvent(event) || "专项专家"}`;
  }
  if (event.type === "tool_event") {
    if (event.tool === "delegate_to_expert") {
      return "专家委派";
    }
    return "工具执行";
  }
  return labelFor(event.agent || event.tool || event.type, agentLabels);
}

function eventSubtitle(event: TimelineEvent) {
  if (event.type === "route_event") {
    return labelFor(event.route, routeLabels) || event.route || "route";
  }
  if (event.type === "tool_event") {
    return labelFor(event.status, statusLabels) || event.type;
  }
  if (event.stage === "delegate_start") {
    return "进入子专家执行";
  }
  return labelFor(event.stage, stageLabels) || labelFor(event.status, statusLabels) || event.type;
}

function asStringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => String(item)).filter((item) => item.trim().length > 0)
    : [];
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "无";
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function PayloadList({ title, items }: { title: string; items: string[] }) {
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

function PayloadFields({ fields }: { fields: Array<[string, unknown]> }) {
  const visible = fields.filter(([, value]) => value !== undefined && value !== null && value !== "");
  if (visible.length === 0) {
    return null;
  }
  return (
    <dl className="event-detail-fields">
      {visible.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{formatValue(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function RequiredParams({ value }: { value: unknown }) {
  if (!Array.isArray(value) || value.length === 0) {
    return null;
  }
  return (
    <div className="event-detail-block">
      <span>必需参数</span>
      <ul>
        {value.map((item, index) => {
          if (!item || typeof item !== "object") {
            return <li key={index}>{formatValue(item)}</li>;
          }
          const data = item as Record<string, unknown>;
          const prompt = formatValue(data.prompt || data.name || `参数 ${index + 1}`);
          const reason = data.reason ? `：${formatValue(data.reason)}` : "";
          return <li key={`${prompt}-${index}`}>{`${prompt}${reason}`}</li>;
        })}
      </ul>
    </div>
  );
}

function EventDetails({ event }: { event: TimelineEvent }) {
  const payload = (event.payload ?? {}) as Record<string, unknown>;
  const todos = asStringList(payload.todos);
  const requiredEvidence = asStringList(payload.required_evidence);
  const gaps = asStringList(payload.gaps);
  const delegatedExpert = expertLabelFromEvent(event);
  const toolName = event.type === "tool_event" ? event.tool : undefined;
  const hasDetails =
    Object.keys(payload).length > 0 ||
    event.duration_ms !== undefined ||
    Boolean(event.usage) ||
    Boolean(event.trace_id) ||
    Boolean(event.span_id);

  if (!hasDetails) {
    return null;
  }

  return (
    <details className="event-details">
      <summary>查看调度详情</summary>
      <PayloadList title="计划步骤" items={todos} />
      <PayloadList title="要求证据" items={requiredEvidence} />
      <RequiredParams value={payload.required_params} />
      <PayloadList title="自检缺口" items={gaps} />
      <PayloadFields
        fields={[
          ["工具名称", toolName],
          ["置信度", payload.confidence],
          ["成功证据数", payload.evidence_count],
          ["失败证据数", payload.failed_evidence_count],
          ["可用工具数", payload.tool_count],
          ["最大步数", payload.max_steps],
          ["历史轮数", payload.history_turns],
          ["Token 估算", payload.token_estimate],
          ["耗时 ms", event.duration_ms],
          ["Trace", event.trace_id],
          ["Span", event.span_id],
          ["委派专家", delegatedExpert],
          ["子任务", payload.subtask],
          ["委派调用", payload.tool_call_id],
          ["工具参数", payload.arguments],
          ["默认值", payload.defaults],
          ["原因", payload.reason],
          ["证据缺口", payload.evidence_gap],
          ["用量", event.usage],
        ]}
      />
    </details>
  );
}

function timelineKey(event: TimelineEvent, index: number) {
  return [
    event.type,
    event.span_id,
    event.evidence_id,
    event.agent,
    event.tool,
    event.stage,
    event.status,
    event.summary,
    index,
  ]
    .filter(Boolean)
    .join("|");
}

function timelineItemClass(event: TimelineEvent) {
  return isDelegateEvent(event) ? "delegate" : undefined;
}

function FeedbackCard({ run, onFeedback }: { run: AgentRun; onFeedback?: FeedbackHandler }) {
  const [correcting, setCorrecting] = useState(false);
  const [rootCause, setRootCause] = useState("");

  if (run.feedback === "adopted") {
    return (
      <div className="panel-card feedback-card">
        <span className="label">反馈</span>
        <p>已采纳，将沉淀为长期经验。</p>
      </div>
    );
  }
  if (run.feedback === "corrected") {
    return (
      <div className="panel-card feedback-card">
        <span className="label">反馈</span>
        <p>已记录纠正，将沉淀为长期经验。</p>
      </div>
    );
  }

  return (
    <div className="panel-card feedback-card">
      <span className="label">这次诊断有帮助吗？</span>
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

export function AgentProcessPanel({ run, onFeedback }: AgentProcessPanelProps) {
  return (
    <section className="agent-panel">
      <header>
        <h2>智能体过程</h2>
        <span className={`status-pill ${run.status}`}>{labelFor(run.status, statusLabels)}</span>
      </header>

      <div className="panel-card">
        <span className="label">路由</span>
        <strong>{labelFor(run.route, routeLabels)}</strong>
        <p>模式：{labelFor(run.mode, modeLabels)}</p>
      </div>

      <div className="panel-card">
        <span className="label">时间线</span>
        {run.events.length === 0 ? (
          <p>暂无事件</p>
        ) : (
          <ol className="timeline">
            {run.events.map((event, index) => {
              const collapsible = event.type === "tool_event";
              const body = (
                <>
                  <p>{event.summary || "事件已记录"}</p>
                  {event.evidence_id ? <code>{event.evidence_id}</code> : null}
                  <EventDetails event={event} />
                </>
              );
              return (
                <li key={timelineKey(event, index)} className={timelineItemClass(event)}>
                  <div className="timeline-icon">{eventIcon(event)}</div>
                  <div>
                    {collapsible ? (
                      <details className="timeline-tool">
                        <summary>
                          <strong>{eventTitle(event)}</strong>
                          <span>{eventSubtitle(event)}</span>
                        </summary>
                        <div className="timeline-tool-body">{body}</div>
                      </details>
                    ) : (
                      <>
                        <strong>{eventTitle(event)}</strong>
                        <span>{eventSubtitle(event)}</span>
                        {body}
                      </>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </div>

      {run.caseId ? (
        <div className="panel-card">
          <span className="label">案例</span>
          <strong>{run.caseId}</strong>
        </div>
      ) : null}

      {run.answer ? (
        <div className="panel-card report-card">
          <span className="label">报告</span>
          <pre>{run.answer}</pre>
        </div>
      ) : null}

      {run.status === "completed" && run.answer ? (
        <FeedbackCard run={run} onFeedback={onFeedback} />
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
