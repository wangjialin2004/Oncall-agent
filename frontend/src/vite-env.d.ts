/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_AGENT_PROCESS_PANEL_V2?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
