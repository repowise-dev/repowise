import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    setupFiles: ["test/setup.ts"],
    coverage: {
      provider: "v8",
      include: ["src/model/**", "src/views/**", "src/data/**"],
      reporter: ["text"],
      thresholds: { lines: 90 },
    },
  },
});
