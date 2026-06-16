import { Activity, CheckCircle2, CircleAlert, Wrench } from "lucide-react";

import type { AgentRun, TimelineEvent } from "../types/events";

type AgentProcessPanelProps = {
  run: AgentRun;
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
  rag: "知识问答",
  aiops: "智能运维",
  clarify: "待澄清",
  unknown: "未知",
};

const modeLabels: Record<string, string> = {
  auto: "自动",
  rag: "知识库",
  oncall: "OnCall",
};

const agentLabels: Record<string, string> = {
  triage: "事件分诊",
  planner: "诊断规划",
  evidence_collector: "证据采集",
  diagnosis: "诊断判断",
  report: "报告生成",
};

const stageLabels: Record<string, string> = {
  triage: "分诊",
  planning: "规划",
  reporting: "报告",
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

export function AgentProcessPanel({ run }: AgentProcessPanelProps) {
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
              <li key={`${event.type}-${event.agent ?? event.tool ?? index}`}>
                <div className="timeline-icon">{eventIcon(event)}</div>
                <div>
                  <strong>{labelFor(event.agent || event.tool || event.type, agentLabels)}</strong>
                  <span>{labelFor(event.stage, stageLabels) || labelFor(event.status, statusLabels) || event.type}</span>
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

      {run.error ? (
        <div className="panel-card error-card">
          <span className="label">错误</span>
          <p>{run.error}</p>
        </div>
      ) : null}
    </section>
  );
}
