import { Activity, CheckCircle2, CircleAlert, Wrench } from "lucide-react";

import type { AgentRun, TimelineEvent } from "../types/events";

type AgentProcessPanelProps = {
  run: AgentRun;
};

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
        <h2>Agent process</h2>
        <span className={`status-pill ${run.status}`}>{run.status}</span>
      </header>

      <div className="panel-card">
        <span className="label">Route</span>
        <strong>{run.route}</strong>
        <p>Mode: {run.mode}</p>
      </div>

      <div className="panel-card">
        <span className="label">Timeline</span>
        {run.events.length === 0 ? (
          <p>No events yet</p>
        ) : (
          <ol className="timeline">
            {run.events.map((event, index) => (
              <li key={`${event.type}-${event.agent ?? event.tool ?? index}`}>
                <div className="timeline-icon">{eventIcon(event)}</div>
                <div>
                  <strong>{event.agent || event.tool || event.type}</strong>
                  <span>{event.stage || event.status || event.type}</span>
                  <p>{event.summary || "Event recorded"}</p>
                  {event.evidence_id ? <code>{event.evidence_id}</code> : null}
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>

      {run.caseId ? (
        <div className="panel-card">
          <span className="label">Case</span>
          <strong>{run.caseId}</strong>
        </div>
      ) : null}

      {run.answer ? (
        <div className="panel-card report-card">
          <span className="label">Report</span>
          <pre>{run.answer}</pre>
        </div>
      ) : null}

      {run.error ? (
        <div className="panel-card error-card">
          <span className="label">Error</span>
          <p>{run.error}</p>
        </div>
      ) : null}
    </section>
  );
}
