import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  // `jsx: "preserve"` in tsconfig leaves esbuild on the classic transform, so
  // any component that does not import React itself fails to render under
  // test. Next compiles the app with the automatic runtime; match it here.
  esbuild: { jsx: "automatic", jsxImportSource: "react" },
  test: {
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
});
