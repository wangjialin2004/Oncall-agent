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

type AgentActivityFeedProps = {
  run: AgentRun;
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
    return <LoaderCircle className="agent-activity-message__spinner" size={15} aria-hidden="true" />;
  }
  if (item.state === "degraded" || item.state === "failed") {
    return <CircleAlert size={15} aria-hidden="true" />;
  }
  return <Check size={15} aria-hidden="true" />;
}

function KindIcon({ item }: { item: InlineActivityItem }) {
  if (item.kind === "tool") return <Wrench size={15} aria-hidden="true" />;
  if (item.kind === "expert-group" || item.kind === "expert") {
    return <Users size={15} aria-hidden="true" />;
  }
  if (item.kind === "report") return <FileText size={15} aria-hidden="true" />;
  return <Activity size={15} aria-hidden="true" />;
}

function hasObservableDetails(item: InlineActivityItem): boolean {
  return Boolean(
    item.details?.some((detail) =>
      ["计划步骤", "所需证据", "当前缺口", "结果摘要", "结果条目", "缺少证据"].includes(
        detail.label,
      ),
    ),
  );
}

function AgentActivityMessage({ item, runId }: { item: InlineActivityItem; runId: string }) {
  const observableDetails = hasObservableDetails(item);
  const [expanded, setExpanded] = useState(item.state === "running" || observableDetails);
  const userToggled = useRef(false);
  const detailId = `agent-activity-detail-${runId}-${item.id}`;
  const duration = formatDuration(item.durationMs);

  useEffect(() => {
    if ((item.state === "running" || observableDetails) && !userToggled.current) {
      setExpanded(true);
    }
  }, [item.state, observableDetails]);

  return (
    <li
      className={`agent-activity-message is-${item.state}${item.depth ? " is-child" : ""}${item.depth === 2 ? " is-grandchild" : ""}`}
      data-agent-activity-message
      data-activity-id={item.id}
      data-activity-kind={item.kind}
      data-activity-state={item.state}
      data-activity-depth={item.depth ?? 0}
    >
      <button
        type="button"
        className="agent-activity-message__toggle"
        aria-expanded={expanded}
        aria-controls={detailId}
        aria-label={`${expanded ? "收起" : "展开"}${item.label}详细信息`}
        onClick={() => {
          userToggled.current = true;
          setExpanded((current) => !current);
        }}
      >
        <span className="agent-activity-message__status-icon">
          <StatusIcon item={item} />
        </span>
        <span className="agent-activity-message__kind-icon">
          <KindIcon item={item} />
        </span>
        <span className="agent-activity-message__title">{item.label}</span>
        <span className="agent-activity-message__meta">
          <span>{statusLabel(item)}</span>
          {duration ? <span>{duration}</span> : null}
        </span>
        <ChevronDown
          className={`agent-activity-message__chevron${expanded ? " is-open" : ""}`}
          size={16}
          aria-hidden="true"
        />
      </button>

      {expanded ? (
        <div
          className="agent-activity-message__details"
          id={detailId}
          role="region"
          aria-label={`${item.label}详细信息`}
        >
          {item.description ? <p>{item.description}</p> : null}
          {item.details?.length ? (
            <dl>
              {item.details.map((detail) => (
                <div key={detail.label}>
                  <dt>{detail.label}</dt>
                  <dd>{detail.value}</dd>
                </div>
              ))}
            </dl>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

export function AgentActivityFeed({ run }: AgentActivityFeedProps) {
  const model = useMemo(() => buildInlineActivityModel(run), [run]);
  const [announcement, setAnnouncement] = useState("");
  const previousCount = useRef(0);

  useEffect(() => {
    if (model.feed.length > previousCount.current) {
      setAnnouncement(`新增活动：${model.feed[model.feed.length - 1]?.label ?? "执行检查"}`);
    }
    previousCount.current = model.feed.length;
  }, [model.feed]);

  if (!model.feed.length) return null;

  return (
    <section className="agent-activity-feed" aria-label="本轮执行活动">
      <header className="agent-activity-feed__header">
        <h3>执行轨迹</h3>
        <span>{model.summary}</span>
      </header>
      <ol className="agent-activity-feed__list">
        {model.feed.map((item) => (
          <AgentActivityMessage item={item} runId={run.runId} key={item.id} />
        ))}
      </ol>
      <span className="sr-only" aria-live="polite">{announcement}</span>
    </section>
  );
}
