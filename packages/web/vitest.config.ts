import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  // The app's own `tsconfig` says `jsx: preserve`, which Next compiles and
  // esbuild cannot, so it falls back to the classic transform and any
  // component rendered from a test throws `React is not defined`.
  esbuild: { jsx: "automatic" },
  test: {
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
});
