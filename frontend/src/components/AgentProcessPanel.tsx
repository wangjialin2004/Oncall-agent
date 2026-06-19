import { useState } from "react";
import { Activity, CheckCircle2, CircleAlert, Wrench } from "lucide-react";

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
  knowledge_expert: "知识问答专家",
  metric_expert: "告警/指标专家",
  log_expert: "日志分析专家",
  change_expert: "变更/发布专家",
  diagnosis: "综合诊断专家",
};

const stageLabels: Record<string, string> = {
  start: "开始",
  complete: "完成",
  error: "出错",
  log_pipeline: "日志预处理",
  log_mapreduce: "日志摘要",
  timeout_fallback: "超时降级",
};

function labelFor(value: string | undefined, labels: Record<string, string>) {
  if (!value) {
    return "";
  }
  return labels[value] || value;
}

function eventIcon(event: TimelineEvent) {
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
  if (event.type === "tool_event") {
    return `工具调用：${event.tool || "unknown"}`;
  }
  return labelFor(event.agent || event.tool || event.type, agentLabels);
}

function eventSubtitle(event: TimelineEvent) {
  if (event.type === "tool_event") {
    return labelFor(event.status, statusLabels) || event.type;
  }
  return labelFor(event.stage, stageLabels) || labelFor(event.status, statusLabels) || event.type;
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
            {run.events.map((event, index) => (
              <li key={timelineKey(event, index)}>
                <div className="timeline-icon">{eventIcon(event)}</div>
                <div>
                  <strong>{eventTitle(event)}</strong>
                  <span>{eventSubtitle(event)}</span>
                  <p>{event.summary || "事件已记录"}</p>
                  {event.evidence_id ? <code>{event.evidence_id}</code> : null}
                </div>
              </li>
            ))}
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
