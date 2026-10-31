import { defineConfig } from "@vscode/test-cli";

export const vscodeVersion = "1.142.0";

export default defineConfig({
  files: "out-test/test/**/*.test.js",
  version: vscodeVersion,
  launchArgs: [
    "--disable-gpu",
    "--disable-workspace-trust",
    "--skip-welcome",
    "--skip-release-notes",
  ],
  // Open an empty fixture folder instead of a real repository so the tests are
  // deterministic: no index is present, so activation never spawns a server or
  // touches a live store.
  workspaceFolder: "src/test/fixtures/empty-workspace",
  mocha: {
    ui: "bdd",
    timeout: 20000,
  },
});
