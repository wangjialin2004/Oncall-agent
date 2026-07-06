import { useEffect, useRef, type ReactNode } from "react";

import { ResizableSplitter } from "./ResizableSplitter";

type AppShellProps = {
  sidebar: ReactNode;
  main: ReactNode;
  panel: ReactNode;
};

const PROCESS_PANEL_MIN = 320;
const PROCESS_PANEL_MAX = 720;
const PROCESS_PANEL_STORAGE_KEY = "superBizAgent.processPanel.width";

function computeDefaultPanelWidth(): number {
  if (typeof window === "undefined") {
    return 360;
  }
  const preferred = Math.round(window.innerWidth * 0.24);
  return Math.min(PROCESS_PANEL_MAX, Math.max(PROCESS_PANEL_MIN, preferred));
}

function densityFromWidth(w: number): "compact" | "standard" | "comfortable" {
  if (w < 360) return "compact";
  if (w > 520) return "comfortable";
  return "standard";
}

export function AppShell({ sidebar, main, panel }: AppShellProps) {
  const panelRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const el = panelRef.current;
    if (!el || typeof window === "undefined" || typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = entry.contentRect.width;
        el.dataset.density = densityFromWidth(w);
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <div className="app-shell">
      <aside className="sidebar">{sidebar}</aside>
      <main className="workspace">{main}</main>
      <ResizableSplitter
        min={PROCESS_PANEL_MIN}
        max={PROCESS_PANEL_MAX}
        defaultWidth={computeDefaultPanelWidth()}
        storageKey={PROCESS_PANEL_STORAGE_KEY}
        targetSelector=".process-panel"
      />
      <aside className="process-panel" ref={panelRef}>{panel}</aside>
    </div>
  );
}
