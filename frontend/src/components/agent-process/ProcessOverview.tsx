import { Clock3, FileSearch, GitFork, Wrench } from "lucide-react";

import type { ProcessPanelModel, ProcessStatus } from "./processModel";

type ProcessOverviewProps = {
  model: ProcessPanelModel;
};

const STATUS_LABELS: Record<ProcessStatus, string> = {
  waiting: "等待开始",
  running: "执行中",
  success: "已完成",
  warning: "需关注",
  error: "执行失败",
  skipped: "已跳过",
};

function formatDuration(ms?: number): string {
  if (ms === undefined) {
    return "计时中";
  }
  if (ms < 1000) {
    return `${Math.round(ms)} ms`;
  }
  return `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)} s`;
}

export function ProcessOverview({ model }: ProcessOverviewProps) {
  const currentStep = model.steps.find((step) => step.id === model.currentStepId);
  return (
    <section className="process-summary" aria-label="本轮过程摘要">
      <div className="process-summary__primary">
        <div>
          <span>诊断路径</span>
          <strong>{model.routeLabel}</strong>
        </div>
        <div>
          <span>{currentStep ? "当前步骤" : "本轮状态"}</span>
          <strong>{currentStep?.title ?? STATUS_LABELS[model.status]}</strong>
        </div>
        <div className="process-summary__duration">
          <Clock3 size={15} aria-hidden="true" />
          <strong>{formatDuration(model.elapsedMs)}</strong>
        </div>
      </div>
      <div className="process-summary__metrics" aria-label="过程计数">
        <span title="工具调用次数">
          <Wrench size={14} aria-hidden="true" />
          {model.counts.tools} 次工具
        </span>
        <span title="有效证据数量">
          <FileSearch size={14} aria-hidden="true" />
          {model.counts.evidence} 条证据
        </span>
        <span title="专家委派次数">
          <GitFork size={14} aria-hidden="true" />
          {model.counts.experts} 次委派
        </span>
      </div>
    </section>
  );
}
