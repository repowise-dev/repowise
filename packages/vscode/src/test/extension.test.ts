import * as assert from "node:assert";
import * as vscode from "vscode";
import { Commands, EXTENSION_ID } from "../constants";
import {
  clearDocDriftLocations,
  lastKnownLines,
  locateReference,
  pruneDocDriftLocations,
} from "../features/locateReference";

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
    // Surfaced in the test output so perf runs can read the measured value.
    console.log(`activation: ${elapsed}ms`);
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

  it("ends the walkthrough on publishing, linked with its source", () => {
    const manifest = getExtension().packageJSON as {
      contributes: {
        walkthroughs: Array<{ steps: Array<{ id: string; description: string }> }>;
        viewsWelcome: Array<{ when: string; contents: string }>;
      };
    };
    const steps = manifest.contributes.walkthroughs[0]?.steps ?? [];
    const last = steps[steps.length - 1];
    assert.ok(last);
    assert.strictEqual(last.id, "shareTeam");
    assert.ok(last.description.includes(`command:${Commands.publish}`));
    const notInstalled = manifest.contributes.viewsWelcome.find((v) =>
      v.when.includes("not-installed"),
    );
    assert.ok(notInstalled?.contents.includes("src=vscode_no_python"));
  });

  it("runs Show Log without throwing", async () => {
    const ext = getExtension();
    await ext.activate();
    // Opening the output channel is inert and must never throw.
    await vscode.commands.executeCommand(Commands.showLog);
  });
});

describe("locateReference", () => {
  it("finds reference on the recorded line", () => {
    const lines = ["# Intro", "See [guide](docs/guide.md) for details.", "End"];
    const res = locateReference(lines, "docs/guide.md", "docs/guide.md", 1);
    assert.deepStrictEqual(res, { line: 1, start: 12, end: 25 });
  });

  it("finds reference after lines were inserted above", () => {
    const lines = [
      "# Title",
      "Extra line 1",
      "Extra line 2",
      "See [guide](docs/guide.md).",
    ];
    // Recorded line 1 before insertion, now at line 3 (+2 lines shifted)
    const res = locateReference(lines, "docs/guide.md", "docs/guide.md", 1);
    assert.deepStrictEqual(res, { line: 3, start: 12, end: 25 });
  });

  it("finds reference after lines were deleted above", () => {
    const lines = ["See [guide](docs/guide.md).", "Next line"];
    // Recorded line 3 before deletion, now at line 0 (-3 lines shifted)
    const res = locateReference(lines, "docs/guide.md", "docs/guide.md", 3);
    assert.deepStrictEqual(res, { line: 0, start: 12, end: 25 });
  });

  it("prefers the nearest match when multiple matches exist", () => {
    // Match at line 3 (distance 2 from recorded line 5) vs match at line 6 (distance 1 from recorded line 5)
    const lines = [
      "line 0",
      "line 1",
      "line 2",
      "docs/guide.md far",
      "line 4",
      "recorded line 5",
      "docs/guide.md near",
    ];
    const res = locateReference(lines, "docs/guide.md", "docs/guide.md", 5);
    assert.strictEqual(res?.line, 6);
  });

  it("prefers the line above on a distance tie", () => {
    // Matches at line 4 (distance 1 above) and line 6 (distance 1 below)
    const lines = [
      "line 0",
      "line 1",
      "line 2",
      "line 3",
      "docs/guide.md above",
      "recorded line 5",
      "docs/guide.md below",
    ];
    const res = locateReference(lines, "docs/guide.md", "docs/guide.md", 5);
    assert.strictEqual(res?.line, 4);
  });

  it("returns null when not found within the search window", () => {
    // Missing completely
    assert.strictEqual(
      locateReference(["line 1", "line 2"], "docs/missing.md", "docs/missing.md", 0),
      null,
    );

    // Farther than ±20 lines away (e.g. at line 35 when recorded line is 5)
    const lines = new Array(50).fill("blank");
    lines[35] = "See docs/guide.md";
    assert.strictEqual(
      locateReference(lines, "docs/guide.md", "docs/guide.md", 5),
      null,
    );
  });

  it("falls back to target when raw is missing or not present", () => {
    const lines = ["Refer to docs/new_guide.md for info."];
    // raw does not match text, but target does
    const res = locateReference(
      lines,
      "docs/old_guide.md",
      "docs/new_guide.md",
      0,
    );
    assert.deepStrictEqual(res, { line: 0, start: 9, end: 26 });

    // raw is empty string
    const resEmptyRaw = locateReference(lines, "", "docs/new_guide.md", 0);
    assert.deepStrictEqual(resEmptyRaw, { line: 0, start: 9, end: 26 });
  });

  it("tracks reference location when searching from updated position across large cumulative inserts", () => {
    // Initial state: recorded line 5
    const lines = new Array(50).fill("line");
    lines[5] = "See docs/guide.md";

    const initial = locateReference(lines, "docs/guide.md", "docs/guide.md", 5);
    assert.strictEqual(initial?.line, 5);

    // First edit: insert 15 lines above (shifted from 5 to 20)
    lines.splice(0, 0, ...new Array(15).fill("inserted line"));
    const firstShift = locateReference(lines, "docs/guide.md", "docs/guide.md", initial!.line);
    assert.strictEqual(firstShift?.line, 20);

    // Second edit: insert another 15 lines above (shifted from 20 to 35, cumulative shift +30 > 20)
    lines.splice(0, 0, ...new Array(15).fill("more lines"));
    // Searching from original recorded line 5 would return null:
    assert.strictEqual(locateReference(lines, "docs/guide.md", "docs/guide.md", 5), null);
    // But searching from the last known line (20) successfully relocates to line 35:
    const secondShift = locateReference(lines, "docs/guide.md", "docs/guide.md", firstShift!.line);
    assert.strictEqual(secondShift?.line, 35);
  });
});

describe("docDriftDiagnostics locations", () => {
  const uri1 = vscode.Uri.file("/workspace/doc1.md");
  const uri2 = vscode.Uri.file("/workspace/doc2.md");

  beforeEach(() => {
    lastKnownLines.clear();
  });

  it("clears document location entries on document drop or closure", () => {
    lastKnownLines.set(`${uri1.toString()}::f1`, 10);
    lastKnownLines.set(`${uri1.toString()}::f2`, 25);
    lastKnownLines.set(`${uri2.toString()}::f3`, 40);

    clearDocDriftLocations(uri1);

    assert.strictEqual(lastKnownLines.has(`${uri1.toString()}::f1`), false);
    assert.strictEqual(lastKnownLines.has(`${uri1.toString()}::f2`), false);
    assert.strictEqual(lastKnownLines.get(`${uri2.toString()}::f3`), 40);
  });

  it("clears all document locations when uri is omitted", () => {
    lastKnownLines.set(`${uri1.toString()}::f1`, 10);
    lastKnownLines.set(`${uri2.toString()}::f3`, 40);

    clearDocDriftLocations();
    assert.strictEqual(lastKnownLines.size, 0);
  });

  it("prunes location entries for findings that are removed", () => {
    lastKnownLines.set(`${uri1.toString()}::f1`, 10);
    lastKnownLines.set(`${uri1.toString()}::f2`, 25);
    lastKnownLines.set(`${uri2.toString()}::f3`, 40);

    // uri1 now only has finding f2
    pruneDocDriftLocations(uri1, [
      { id: "f2", line_number: 26, confidence: 0.9, kind: "drift", reason: "r", raw: "x", target: "y" } as any,
    ]);

    assert.strictEqual(lastKnownLines.has(`${uri1.toString()}::f1`), false);
    assert.strictEqual(lastKnownLines.get(`${uri1.toString()}::f2`), 25);
    assert.strictEqual(lastKnownLines.get(`${uri2.toString()}::f3`), 40);
  });
});


