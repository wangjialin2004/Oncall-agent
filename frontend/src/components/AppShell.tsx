import type { ReactNode } from "react";

type AppShellProps = {
  sidebar: ReactNode;
  main: ReactNode;
  panel: ReactNode;
};

export function AppShell({ sidebar, main, panel }: AppShellProps) {
  return (
    <div className="app-shell">
      <aside className="sidebar">{sidebar}</aside>
      <main className="workspace">{main}</main>
      <aside className="process-panel">{panel}</aside>
    </div>
  );
}
