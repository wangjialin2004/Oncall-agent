import { MessageSquarePlus } from "lucide-react";

type SidebarProps = {
  onNewSession: () => void;
};

export function Sidebar({ onNewSession }: SidebarProps) {
  return (
    <div className="sidebar-inner">
      <h1>Agent Gateway</h1>
      <button className="sidebar-action" type="button" onClick={onNewSession}>
        <MessageSquarePlus size={18} aria-hidden="true" />
        New session
      </button>
      <div className="sidebar-section">
        <span>Recent sessions</span>
        <p>No saved sessions yet</p>
      </div>
    </div>
  );
}
