import { useState } from "react";

import type { AgentRun } from "../../types/events";

type RunOutcomeProps = {
  run: AgentRun;
  onFeedback?: (kind: "adopted" | "corrected", actualRootCause?: string) => void;
  onDistill?: (action: "confirm" | "reject") => void;
  onConfirmSuggestion?: (actionId: string) => void;
};

function checkpointMode(run: AgentRun): string {
  const resume = run.checkpointResume;
  if (!resume) return "";
  if (resume.replayOverride === true) return "激进模式：本次主动重放了受限工具。";
  if (resume.replayOverride === false) {
    return "强制保守：本次明确拒绝重放非白名单工具。";
  }
  return resume.conservative
    ? "保守模式：默认未重放非白名单工具。"
    : "激进模式：按服务端默认重放了工具。";
}

export function RunOutcome({
  run,
  onFeedback,
  onDistill,
  onConfirmSuggestion,
}: RunOutcomeProps) {
  const [correcting, setCorrecting] = useState(false);
  const [rootCause, setRootCause] = useState("");

  return (
    <>
      {run.checkpointResume ? (
        <div className="inline-run-note" role="status" data-testid="checkpoint-resume-banner">
          已从第 {run.checkpointResume.resumedFromStep} 步检查点恢复，已载入
          {run.checkpointResume.replayedSteps} 步记录。{checkpointMode(run)}
        </div>
      ) : null}
      {run.checkpointConservativeClose ? (
        <div
          className="inline-run-note is-caution"
          role="status"
          data-testid="checkpoint-conservative-close-banner"
        >
          已基于落盘证据保守收口，未自动重放受限工具。
        </div>
      ) : null}

      {run.status === "completed" && (run.suggestedActions?.length ?? 0) > 0 ? (
        <div className="inline-run-actions" data-testid="suggested-actions-card">
          <span className="inline-run-actions__label">建议动作</span>
          {run.suggestedActions?.map((action) => {
            const confirmed = (run.confirmedActionIds ?? []).includes(action.id);
            return (
              <div className="inline-run-actions__row" key={action.id}>
                <span>{action.title}</span>
                {confirmed ? (
                  <span className="inline-run-actions__confirmed" role="status">
                    已确认（未执行）
                  </span>
                ) : (
                  <button type="button" onClick={() => onConfirmSuggestion?.(action.id)}>
                    确认建议
                  </button>
                )}
              </div>
            );
          })}
        </div>
      ) : null}

      {run.status === "completed" && run.answer && run.userMessage ? (
        <div className="inline-run-actions" data-testid="feedback-card">
          {run.feedback === "adopted" || run.distillStatus === "confirmed" ? (
            <span className="inline-run-actions__confirmed">已采纳，将沉淀为长期经验。</span>
          ) : run.feedback === "corrected" ? (
            <span className="inline-run-actions__confirmed">已记录纠正，将沉淀为长期经验。</span>
          ) : correcting ? (
            <>
              <label className="inline-run-actions__correction">
                <span>实际根因</span>
                <textarea
                  aria-label="纠正根因"
                  value={rootCause}
                  onChange={(event) => setRootCause(event.target.value)}
                />
              </label>
              <div className="inline-run-actions__buttons">
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
            </>
          ) : (
            <div className="inline-run-actions__buttons">
              <button
                type="button"
                onClick={() =>
                  run.distillDraft?.status === "pending"
                    ? onDistill?.("confirm")
                    : onFeedback?.("adopted")
                }
              >
                采纳
              </button>
              <button type="button" className="ghost" onClick={() => setCorrecting(true)}>
                纠正
              </button>
              {run.distillDraft?.status === "pending" && !run.distillStatus ? (
                <button type="button" className="ghost" onClick={() => onDistill?.("reject")}>
                  拒绝草稿
                </button>
              ) : null}
            </div>
          )}
        </div>
      ) : null}
    </>
  );
}
