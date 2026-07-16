import { useState } from "react";
import {
  Activity,
  Braces,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  CircleDashed,
  FileSearch,
  GitBranch,
  ListChecks,
  Wrench,
} from "lucide-react";

import type { ProcessDetailSection } from "./processContent";
import type { ProcessActivity, ProcessPhase, ProcessStatus, ProcessStep } from "./processModel";

type ProcessTimelineProps = {
  steps: ProcessStep[];
  currentStepId?: string;
};

const STATUS_LABELS: Record<ProcessStatus, string> = {
  waiting: "等待中",
  running: "进行中",
  success: "已完成",
  warning: "需关注",
  error: "失败",
  skipped: "已跳过",
};

function formatDuration(ms?: number): string {
  if (ms === undefined) {
    return "";
  }
  if (ms < 1000) {
    return `${Math.round(ms)} ms`;
  }
  return `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)} s`;
}

function PhaseIcon({ phase, status }: { phase: ProcessPhase; status: ProcessStatus }) {
  if (status === "error" || status === "warning") {
    return <CircleAlert size={16} aria-hidden="true" />;
  }
  if (status === "success") {
    return <CheckCircle2 size={16} aria-hidden="true" />;
  }
  if (phase === "route") {
    return <GitBranch size={16} aria-hidden="true" />;
  }
  if (phase === "plan") {
    return <ListChecks size={16} aria-hidden="true" />;
  }
  if (phase === "verify") {
    return <FileSearch size={16} aria-hidden="true" />;
  }
  if (phase === "execute" || phase === "retry") {
    return <Wrench size={16} aria-hidden="true" />;
  }
  if (status === "waiting") {
    return <CircleDashed size={16} aria-hidden="true" />;
  }
  return <Activity size={16} aria-hidden="true" />;
}

function DetailFields({ section }: { section: ProcessDetailSection }) {
  return section.fields.length > 0 ? (
    <dl className="process-detail-fields">
      {section.fields.map((field) => (
        <div key={`${field.label}-${field.value}`}>
          <dt>{field.label}</dt>
          <dd>{field.value}</dd>
        </div>
      ))}
    </dl>
  ) : null;
}

function DetailContent({ section }: { section: ProcessDetailSection }) {
  const showSummary =
    Boolean(section.summary) &&
    (section.kind === "input" || (section.fields.length === 0 && section.items.length === 0));
  return (
    <>
      {showSummary ? <p className="process-detail-summary">{section.summary}</p> : null}
      <DetailFields section={section} />
      {section.items.length > 0 ? (
        <ul className="process-detail-items">
          {section.items.map((item, index) => (
            <li key={`${index}-${item}`}>{item}</li>
          ))}
        </ul>
      ) : null}
      {section.remaining ? <p className="process-detail-more">另有 {section.remaining} 条未展开</p> : null}
      {section.raw ? <pre className="process-detail-raw">{section.raw}</pre> : null}
    </>
  );
}

function DetailSection({ section, ownerTitle }: { section: ProcessDetailSection; ownerTitle: string }) {
  const [technicalOpen, setTechnicalOpen] = useState(false);
  if (section.kind === "technical") {
    return (
      <section className="process-detail-section process-technical-details">
        <button
          type="button"
          className="process-technical-toggle"
          aria-expanded={technicalOpen}
          aria-label={`${technicalOpen ? "收起" : "查看"}${ownerTitle}技术信息`}
          onClick={() => setTechnicalOpen((open) => !open)}
        >
          <Braces size={14} aria-hidden="true" />
          <span>技术信息</span>
          <ChevronDown size={14} aria-hidden="true" />
        </button>
        {technicalOpen ? <DetailContent section={section} /> : null}
      </section>
    );
  }
  return (
    <section className={`process-detail-section kind-${section.kind}`}>
      <h4>{section.title}</h4>
      <DetailContent section={section} />
    </section>
  );
}

function ActivityDetails({
  activity,
  ownerTitle,
  showHeader,
}: {
  activity: ProcessActivity;
  ownerTitle: string;
  showHeader: boolean;
}) {
  return (
    <section className="process-activity">
      {showHeader ? (
        <header>
          <strong>{activity.title}</strong>
          <span className={`process-activity__status status-${activity.status}`}>
            {STATUS_LABELS[activity.status]}
            {activity.durationMs !== undefined ? ` · ${formatDuration(activity.durationMs)}` : ""}
          </span>
        </header>
      ) : null}
      {activity.summary && activity.details.length === 0 ? <p>{activity.summary}</p> : null}
      {activity.details.map((section) => (
        <DetailSection key={`${activity.id}-${section.id}`} section={section} ownerTitle={ownerTitle} />
      ))}
    </section>
  );
}

function ProcessStepRow({ step, defaultExpanded }: { step: ProcessStep; defaultExpanded: boolean }) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const detailId = `process-step-details-${step.id}`;
  const hasDetails = step.details.length > 0 || step.activities.length > 0;

  return (
    <li className={`process-step-row phase-${step.phase} status-${step.status}`} data-step-id={step.id}>
      <div className="process-step-row__rail" aria-hidden="true">
        <span className="process-step-row__icon">
          <PhaseIcon phase={step.phase} status={step.status} />
        </span>
      </div>
      <div className="process-step-row__body">
        <header className="process-step-row__header">
          <div className="process-step-row__title">
            <span className="process-step-row__index">{String(step.order).padStart(2, "0")}</span>
            <strong>{step.title}</strong>
          </div>
          <div className="process-step-row__meta">
            <span className={`process-step-status status-${step.status}`}>{STATUS_LABELS[step.status]}</span>
            {step.durationMs !== undefined ? <time>{formatDuration(step.durationMs)}</time> : null}
            {hasDetails ? (
              <button
                type="button"
                className="process-step-toggle"
                aria-expanded={expanded}
                aria-controls={detailId}
                aria-label={`${expanded ? "收起" : "查看"}${step.title}详情`}
                title={`${expanded ? "收起" : "查看"}${step.title}详情`}
                onClick={() => setExpanded((open) => !open)}
              >
                <ChevronDown size={16} aria-hidden="true" />
              </button>
            ) : null}
          </div>
        </header>
        {step.summary ? <p className="process-step-row__summary">{step.summary}</p> : null}
        {expanded && hasDetails ? (
          <div id={detailId} className="process-step-details">
            {step.details.map((section) => (
              <DetailSection key={`${step.id}-${section.id}`} section={section} ownerTitle={step.title} />
            ))}
            {step.activities.length > 0 ? (
              <div className="process-activities" aria-label={`${step.title}活动`}>
                {step.activities.map((activity) => (
                  <ActivityDetails
                    key={activity.id}
                    activity={activity}
                    ownerTitle={activity.title}
                    showHeader={step.activities.length > 1 || activity.title !== step.title}
                  />
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </li>
  );
}

export function ProcessTimeline({ steps, currentStepId }: ProcessTimelineProps) {
  if (steps.length === 0) {
    return <p className="process-empty">正在准备本轮执行过程…</p>;
  }
  return (
    <ol className="process-steps" aria-label="本轮执行步骤">
      {steps.map((step) => (
        <ProcessStepRow
          key={step.id}
          step={step}
          defaultExpanded={
            step.id === currentStepId || step.status === "warning" || step.status === "error"
          }
        />
      ))}
    </ol>
  );
}
