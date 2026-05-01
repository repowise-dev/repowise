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
      "@repowise/types": path.resolve(__dirname, "../types/src/index.ts"),
      "@repowise/types/graph": path.resolve(__dirname, "../types/src/graph.ts"),
      "@repowise/types/git": path.resolve(__dirname, "../types/src/git.ts"),
      "@repowise/types/docs": path.resolve(__dirname, "../types/src/docs.ts"),
      "@repowise/types/decisions": path.resolve(__dirname, "../types/src/decisions.ts"),
      "@repowise/types/dead-code": path.resolve(__dirname, "../types/src/dead-code.ts"),
      "@repowise/types/symbols": path.resolve(__dirname, "../types/src/symbols.ts"),
      "@repowise/types/chat": path.resolve(__dirname, "../types/src/chat.ts"),
    },
  },
});
