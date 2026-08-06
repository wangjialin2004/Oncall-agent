import { useEffect, useRef, useState } from "react";
import { Activity, CircleDot, Gauge, LogOut, MessageSquarePlus, Trash2 } from "lucide-react";

import type { ConversationSummary } from "../api/conversationApi";
import type { CheckpointSummary } from "../api/checkpointApi";

type SidebarView = "chat" | "baseline";

type SidebarProps = {
  username: string;
  activeView: SidebarView;
  sessions: ConversationSummary[];
  activeSessionId: string;
  checkpointStatus: Record<string, CheckpointSummary>;
  onNewSession: () => void;
  onSelectSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
  onOpenBaselines: () => void;
  onLogout: () => void;
};

/** Keep in sync with `.session-item.is-exiting` animation duration in styles.css. */
export const SESSION_EXIT_MS = 320;

export function Sidebar({
  username,
  activeView,
  sessions,
  activeSessionId,
  checkpointStatus,
  onNewSession,
  onSelectSession,
  onDeleteSession,
  onOpenBaselines,
  onLogout,
}: SidebarProps) {
  const avatarLetter = username.charAt(0).toUpperCase() || "U";
  // Sessions mid-exit stay mounted so the collapse is visible even in long lists.
  const [exitingIds, setExitingIds] = useState<Set<string>>(() => new Set());
  const exitTimersRef = useRef<Map<string, number>>(new Map());
  const itemRefs = useRef<Map<string, HTMLLIElement | null>>(new Map());

  useEffect(() => {
    const timers = exitTimersRef.current;
    return () => {
      for (const timer of timers.values()) {
        window.clearTimeout(timer);
      }
      timers.clear();
    };
  }, []);

  // Drop exit bookkeeping for sessions that left the list without us (e.g. external refresh).
  useEffect(() => {
    const live = new Set(sessions.map((session) => session.session_id));
    setExitingIds((current) => {
      let changed = false;
      const next = new Set<string>();
      for (const id of current) {
        if (live.has(id)) {
          next.add(id);
        } else {
          changed = true;
          const timer = exitTimersRef.current.get(id);
          if (timer !== undefined) {
            window.clearTimeout(timer);
            exitTimersRef.current.delete(id);
          }
        }
      }
      return changed ? next : current;
    });
  }, [sessions]);

  function handleDeleteClick(sessionId: string) {
    if (exitingIds.has(sessionId) || exitTimersRef.current.has(sessionId)) {
      return;
    }

    // Scroll the row into view so a delete deep in a long list is still obvious.
    const row = itemRefs.current.get(sessionId);
    row?.scrollIntoView({ block: "nearest", behavior: "smooth" });

    setExitingIds((current) => {
      const next = new Set(current);
      next.add(sessionId);
      return next;
    });

    const timer = window.setTimeout(() => {
      exitTimersRef.current.delete(sessionId);
      setExitingIds((current) => {
        if (!current.has(sessionId)) {
          return current;
        }
        const next = new Set(current);
        next.delete(sessionId);
        return next;
      });
      onDeleteSession(sessionId);
    }, SESSION_EXIT_MS);
    exitTimersRef.current.set(sessionId, timer);
  }

  return (
    <div className="sidebar-inner">
      <div className="sidebar-brand">
        <div className="sidebar-brand-icon">
          <Activity size={15} color="#fff" aria-hidden="true" />
        </div>
        <h1>Agent Gateway</h1>
      </div>

      <button className="sidebar-action" type="button" onClick={onNewSession}>
        <MessageSquarePlus size={15} aria-hidden="true" />
        新建会话
      </button>

      <button
        className={`sidebar-action${activeView === "baseline" ? " active" : ""}`}
        type="button"
        onClick={onOpenBaselines}
      >
        <Gauge size={15} aria-hidden="true" />
        服务基线
      </button>

      <div className="sidebar-section">
        <span className="sidebar-section-label">历史会话</span>
        {sessions.length === 0 ? (
          <p>暂无历史记录</p>
        ) : (
          <ul className="session-list">
            {sessions.map((session) => {
              const isActive = activeView === "chat" && session.session_id === activeSessionId;
              const resumable = checkpointStatus[session.session_id]?.resumable === true;
              const isExiting = exitingIds.has(session.session_id);
              return (
                <li
                  key={session.session_id}
                  ref={(node) => {
                    if (node) {
                      itemRefs.current.set(session.session_id, node);
                    } else {
                      itemRefs.current.delete(session.session_id);
                    }
                  }}
                  className={`session-item${isActive ? " active" : ""}${
                    resumable ? " resumable" : ""
                  }${isExiting ? " is-exiting" : ""}`}
                  aria-busy={isExiting || undefined}
                >
                  <button
                    type="button"
                    className="session-open"
                    title={session.title}
                    disabled={isExiting}
                    onClick={() => onSelectSession(session.session_id)}
                  >
                    <span className="session-title">{session.title || "未命名会话"}</span>
                    <span className="session-meta">
                      {resumable ? (
                        <span className="session-resumable-badge" aria-label="可继续未完成排查">
                          <CircleDot size={12} aria-hidden="true" />
                          可继续
                        </span>
                      ) : null}
                      <span className="session-turn-count">{session.turn_count} 轮</span>
                    </span>
                  </button>
                  <button
                    type="button"
                    className="session-delete"
                    aria-label={`删除会话 ${session.title}`}
                    disabled={isExiting}
                    onClick={(event) => {
                      // Keep the sibling "open session" control from also
                      // handling this gesture if layout/event paths change.
                      event.preventDefault();
                      event.stopPropagation();
                      handleDeleteClick(session.session_id);
                    }}
                  >
                    <Trash2 size={13} aria-hidden="true" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="sidebar-footer">
        <div className="sidebar-user">
          <div className="sidebar-user-avatar" aria-hidden="true">
            {avatarLetter}
          </div>
          <span className="sidebar-username">{username}</span>
        </div>
        <button className="sidebar-logout" type="button" onClick={onLogout}>
          <LogOut size={13} aria-hidden="true" />
          退出登录
        </button>
      </div>
    </div>
  );
}
