/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SHOW_CONTENT?: string;
  readonly VITE_GRAFANA_URL?: string;
  readonly VITE_PROJECT_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
