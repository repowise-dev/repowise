import { defineConfig } from "@vscode/test-cli";

export default defineConfig({
  files: "out-test/test/**/*.test.js",
  version: "stable",
  // Open an empty fixture folder instead of a real repository so the tests are
  // deterministic: no index is present, so activation never spawns a server or
  // touches a live store.
  workspaceFolder: "src/test/fixtures/empty-workspace",
  mocha: {
    ui: "bdd",
    timeout: 20000,
  },
});
