import { Activity, LogOut, MessageSquarePlus } from "lucide-react";

type SidebarProps = {
  username: string;
  onNewSession: () => void;
  onLogout: () => void;
};

export function Sidebar({ username, onNewSession, onLogout }: SidebarProps) {
  const avatarLetter = username.charAt(0).toUpperCase() || "U";

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

      <div className="sidebar-section">
        <span className="sidebar-section-label">历史会话</span>
        <p>暂无历史记录</p>
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
