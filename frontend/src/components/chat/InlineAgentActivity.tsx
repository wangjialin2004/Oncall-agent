import {
  Activity,
  Check,
  ChevronDown,
  CircleAlert,
  FileText,
  LoaderCircle,
  Users,
  Wrench,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { AgentRun } from "../../types/events";
import {
  buildInlineActivityModel,
  type InlineActivityItem,
} from "./inlineActivityModel";
import { RunOutcome } from "./RunOutcome";

type InlineAgentActivityProps = {
  run: AgentRun;
  onFeedback?: (kind: "adopted" | "corrected", actualRootCause?: string) => void;
  onDistill?: (action: "confirm" | "reject") => void;
  onConfirmSuggestion?: (actionId: string) => void;
};

function formatDuration(durationMs?: number): string {
  if (durationMs === undefined) return "";
  if (durationMs < 1000) return `${Math.round(durationMs)} 毫秒`;
  return `${(durationMs / 1000).toFixed(1)} 秒`;
}

function statusLabel(item: InlineActivityItem): string {
  if (item.state === "running") return "进行中";
  if (item.state === "queued") return "等待中";
  if (item.state === "degraded") return "已降级继续";
  if (item.state === "failed") return "失败";
  return "已完成";
}

function StatusIcon({ item }: { item: InlineActivityItem }) {
  if (item.state === "running") {
    return <LoaderCircle className="inline-activity__spinner" size={15} aria-hidden="true" />;
  }
  if (item.state === "degraded" || item.state === "failed") {
    return <CircleAlert size={15} aria-hidden="true" />;
  }
  return <Check size={15} aria-hidden="true" />;
}

function KindIcon({ item }: { item: InlineActivityItem }) {
  if (item.kind === "tool") return <Wrench size={14} aria-hidden="true" />;
  if (item.kind === "expert-group" || item.kind === "expert") {
    return <Users size={14} aria-hidden="true" />;
  }
  if (item.kind === "report") return <FileText size={14} aria-hidden="true" />;
  return <Activity size={14} aria-hidden="true" />;
}

function ActivityRow({ item, child = false }: { item: InlineActivityItem; child?: boolean }) {
  const duration = formatDuration(item.durationMs);
  return (
    <div
      className={`inline-activity__row${child ? " is-child" : ""} is-${item.state}`}
      data-activity-kind={item.kind}
    >
      <span className="inline-activity__status-icon">
        <StatusIcon item={item} />
      </span>
      <span className="inline-activity__kind-icon">
        <KindIcon item={item} />
      </span>
      <span className="inline-activity__label">{item.label}</span>
      <span className="inline-activity__meta">
        <span>{statusLabel(item)}</span>
        {duration ? <span>{duration}</span> : null}
      </span>
    </div>
  );
}

function ActivityBranch({ item, child = false }: { item: InlineActivityItem; child?: boolean }) {
  return (
    <div className="inline-activity__step">
      <ActivityRow item={item} child={child} />
      {item.children?.length ? (
        <div className="inline-activity__children">
          {item.children.map((nested) => (
            <ActivityBranch item={nested} child key={nested.id} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function InlineAgentActivity({
  run,
  onFeedback,
  onDistill,
  onConfirmSuggestion,
}: InlineAgentActivityProps) {
  const model = useMemo(() => buildInlineActivityModel(run), [run]);
  const [expanded, setExpanded] = useState(run.status === "running");
  const [startedAt, setStartedAt] = useState(() => Date.now());
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const previousStatus = useRef(run.status);
  const userToggled = useRef(false);

  useEffect(() => {
    setStartedAt(Date.now());
    setElapsedSeconds(0);
    userToggled.current = false;
  }, [run.runId]);

  useEffect(() => {
    const previous = previousStatus.current;
    if (run.status === "running") {
      setExpanded(true);
    } else if (run.status === "completed" && previous === "running" && !userToggled.current) {
      setExpanded(false);
    } else if (run.status === "error" || run.status === "cancelled") {
      setExpanded(true);
    }
    previousStatus.current = run.status;
  }, [run.status]);

  useEffect(() => {
    if (run.status !== "running") return undefined;
    const update = () => setElapsedSeconds(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)));
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [run.status, startedAt]);

  return (
    <section className={`inline-activity is-${run.status}`} aria-live="polite">
      <button
        className="inline-activity__toggle"
        type="button"
        aria-expanded={expanded}
        aria-controls={`activity-${run.runId}`}
        onClick={() => {
          userToggled.current = true;
          setExpanded((current) => !current);
        }}
      >
        <span className="inline-activity__summary-status">
          {run.status === "running" ? (
            <LoaderCircle className="inline-activity__spinner" size={16} aria-hidden="true" />
          ) : run.status === "error" ? (
            <CircleAlert size={16} aria-hidden="true" />
          ) : (
            <Check size={16} aria-hidden="true" />
          )}
        </span>
        <span className="inline-activity__summary">{model.summary}</span>
        {run.status === "running" ? (
          <span className="inline-activity__elapsed">已进行 {elapsedSeconds} 秒</span>
        ) : null}
        <ChevronDown
          className={`inline-activity__chevron${expanded ? " is-open" : ""}`}
          size={16}
          aria-hidden="true"
        />
      </button>

      {expanded ? (
        <div
          className="inline-activity__body"
          id={`activity-${run.runId}`}
          role="region"
          aria-label="助手执行过程"
        >
          {model.items.map((item) => (
            <ActivityBranch item={item} key={item.id} />
          ))}
        </div>
      ) : null}

      <RunOutcome
        run={run}
        onFeedback={onFeedback}
        onDistill={onDistill}
        onConfirmSuggestion={onConfirmSuggestion}
      />
    </section>
  );
}
