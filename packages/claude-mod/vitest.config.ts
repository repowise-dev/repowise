import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    coverage: {
      provider: "v8",
      include: ["src/model/**", "src/views/**"],
      reporter: ["text"],
      thresholds: { lines: 90 },
    },
  },
});
