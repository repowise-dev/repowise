import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./__tests__/setup.ts"],
    include: ["__tests__/**/*.{test,spec}.{ts,tsx}", "src/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["node_modules", "dist"],
  },
  resolve: {
    alias: {
      "@repowise-dev/types": path.resolve(__dirname, "../types/src/index.ts"),
      "@repowise-dev/types/graph": path.resolve(__dirname, "../types/src/graph.ts"),
      "@repowise-dev/types/git": path.resolve(__dirname, "../types/src/git.ts"),
      "@repowise-dev/types/docs": path.resolve(__dirname, "../types/src/docs.ts"),
      "@repowise-dev/types/decisions": path.resolve(__dirname, "../types/src/decisions.ts"),
      "@repowise-dev/types/dead-code": path.resolve(__dirname, "../types/src/dead-code.ts"),
      "@repowise-dev/types/symbols": path.resolve(__dirname, "../types/src/symbols.ts"),
      "@repowise-dev/types/chat": path.resolve(__dirname, "../types/src/chat.ts"),
    },
  },
});
