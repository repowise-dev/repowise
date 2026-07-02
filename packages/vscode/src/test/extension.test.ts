import * as assert from "node:assert";
import * as vscode from "vscode";
import { Commands, EXTENSION_ID } from "../constants";

/**
 * Fast, offline smoke suite. It runs inside a real VS Code process against an
 * empty fixture workspace, so nothing here reaches the network, spawns the CLI,
 * or starts the local server.
 */
describe("Repowise extension", () => {
  function getExtension(): vscode.Extension<unknown> {
    const ext = vscode.extensions.getExtension(EXTENSION_ID);
    assert.ok(ext, `extension ${EXTENSION_ID} not found`);
    return ext;
  }

  it("is present", () => {
    getExtension();
  });

  it("activates quickly and without spawning a subprocess", async () => {
    // Activation must do no blocking work: at most one filesystem stat, no CLI
    // spawn, no server start. We cannot spawn-count from here, so we assert the
    // observable proxy: activation resolves well within a couple of seconds.
    // The strict sub-50ms budget is checked by hand via the built-in
    // "Developer: Startup Performance" report.
    const ext = getExtension();
    const start = Date.now();
    await ext.activate();
    const elapsed = Date.now() - start;
    assert.strictEqual(ext.isActive, true);
    assert.ok(
      elapsed < 2000,
      `activation took ${elapsed}ms, expected under 2000ms`,
    );
  });

  it("registers every command it contributes", async () => {
    const ext = getExtension();
    await ext.activate();

    // Source of truth for what should be registered is the manifest itself, so
    // this fails if a contributed command is ever left unregistered.
    const manifest = ext.packageJSON as {
      contributes?: { commands?: Array<{ command: string }> };
    };
    const contributed = (manifest.contributes?.commands ?? []).map(
      (c) => c.command,
    );
    assert.strictEqual(
      contributed.length,
      Object.keys(Commands).length,
      "manifest command count drifted from the Commands map",
    );

    const registered = await vscode.commands.getCommands(true);
    for (const command of contributed) {
      assert.ok(
        registered.includes(command),
        `command not registered: ${command}`,
      );
    }
  });

  it("runs Show Log without throwing", async () => {
    const ext = getExtension();
    await ext.activate();
    // Opening the output channel is inert and must never throw.
    await vscode.commands.executeCommand(Commands.showLog);
  });
});
