import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const agentGatewayTarget = process.env.AGENT_GATEWAY_API_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: agentGatewayTarget,
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: [],
  },
});
