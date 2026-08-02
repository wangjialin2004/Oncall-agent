/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_AGENT_PROCESS_PANEL_V2?: string;
  readonly VITE_INLINE_AGENT_ACTIVITY_ENABLED?: string;
  readonly VITE_GRANULAR_AGENT_ACTIVITY_ENABLED?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
