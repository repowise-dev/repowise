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
    // Subpath aliases MUST precede the bare "@repowise-dev/types" entry:
    // @rollup/plugin-alias matches the first entry whose `find` prefixes the
    // import (`importee === find || importee.startsWith(find + "/")`), so a
    // leading bare alias shadows every `/subpath` and rewrites e.g.
    // `@repowise-dev/types/health` → `<…/index.ts>/health`. That only breaks
    // runtime *value* imports (type-only imports are erased), which is why it
    // stayed latent until a test transitively value-imported a types subpath.
    // Keep this list in sync with packages/types/package.json `exports`.
    alias: {
      "@repowise-dev/types/graph": path.resolve(__dirname, "../types/src/graph.ts"),
      "@repowise-dev/types/git": path.resolve(__dirname, "../types/src/git.ts"),
      "@repowise-dev/types/docs": path.resolve(__dirname, "../types/src/docs.ts"),
      "@repowise-dev/types/decisions": path.resolve(__dirname, "../types/src/decisions.ts"),
      "@repowise-dev/types/dead-code": path.resolve(__dirname, "../types/src/dead-code.ts"),
      "@repowise-dev/types/symbols": path.resolve(__dirname, "../types/src/symbols.ts"),
      "@repowise-dev/types/chat": path.resolve(__dirname, "../types/src/chat.ts"),
      "@repowise-dev/types/workspace": path.resolve(__dirname, "../types/src/workspace.ts"),
      "@repowise-dev/types/blast-radius": path.resolve(__dirname, "../types/src/blast-radius.ts"),
      "@repowise-dev/types/jobs": path.resolve(__dirname, "../types/src/jobs.ts"),
      "@repowise-dev/types/settings": path.resolve(__dirname, "../types/src/settings.ts"),
      "@repowise-dev/types/security": path.resolve(__dirname, "../types/src/security.ts"),
      "@repowise-dev/types/owners": path.resolve(__dirname, "../types/src/owners.ts"),
      "@repowise-dev/types/modules": path.resolve(__dirname, "../types/src/modules.ts"),
      "@repowise-dev/types/overview": path.resolve(__dirname, "../types/src/overview.ts"),
      "@repowise-dev/types/files": path.resolve(__dirname, "../types/src/files.ts"),
      "@repowise-dev/types/external-systems": path.resolve(
        __dirname,
        "../types/src/external-systems.ts",
      ),
      "@repowise-dev/types/health": path.resolve(__dirname, "../types/src/health.ts"),
      "@repowise-dev/types/coupling": path.resolve(__dirname, "../types/src/coupling.ts"),
      "@repowise-dev/types/stats": path.resolve(__dirname, "../types/src/stats.ts"),
      "@repowise-dev/types": path.resolve(__dirname, "../types/src/index.ts"),
    },
  },
});
