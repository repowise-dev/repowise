import { describe, expect, it } from "vitest";
import { agentListRows, deriveSetup, type DeriveOptions, type SetupRow } from "./setup-rows";
import { doneFixture, NOW, setupFixture } from "./setup-fixture";

const OPTS: DeriveOptions = { repoName: "orbit-api", now: NOW, relative: (iso) => `@${iso}` };
const ids = (rows: SetupRow[]) => rows.map((r) => r.id);

describe("deriveSetup", () => {
  it("splits rows into essentials and optional, with no attention when nothing is wrong", () => {
    const view = deriveSetup(setupFixture(), OPTS);
    expect(view.attention).toEqual([]);
    expect(ids(view.essentials)).toEqual(["index", "agent", "sync"]);
    expect(ids(view.optional)).toEqual(["model", "coverage", "rewrite"]);
    expect(view.doneCount).toBe(2);
    expect(view.allDone).toBe(false);
  });

  it("offers to connect an installed agent while a registered one waits for its first call", () => {
    const agent = deriveSetup(setupFixture(), OPTS).essentials[1]!;
    expect(agent.tone).toBe("waiting");
    expect(agent.detail).toEqual({
      key: "agent.detailWaitingOther",
      values: { agents: "Claude Code", count: 1, other: "Cursor" },
    });
    expect(agent.action).toEqual({ kind: "connect", agentId: "cursor", agentName: "Cursor" });
    expect(agent.command).toBe("repowise agents add --target=cursor");
  });

  it("is done when the index is current, an agent has called and sync is on", () => {
    const view = deriveSetup(doneFixture(), OPTS);
    expect(view.doneCount).toBe(3);
    expect(view.allDone).toBe(true);
    expect(view.essentials.every((r) => r.tone === "done")).toBe(true);
    expect(view.essentials[1]!.detail).toEqual({
      key: "agent.detailCalled",
      values: { agent: "Claude Code", when: "@2026-10-25T11:56:00Z" },
    });
  });

  it("moves an index with skipped files into Needs attention, with the files", () => {
    const s = setupFixture();
    const view = deriveSetup(
      { ...s, index: { ...s.index, parse_error_count: 2, parse_error_files: ["a.py", "b.py"] } },
      OPTS,
    );
    expect(ids(view.attention)).toEqual(["index-parse"]);
    expect(ids(view.essentials)).toEqual(["agent", "sync"]);
    const row = view.attention[0]!;
    expect(row.action).toEqual({ kind: "prompt" });
    expect(row.files).toEqual(["a.py", "b.py"]);
    expect(row.prompt).toMatchObject({ fix: true, verify: "doctor", items: ["a.py", "b.py"] });
    // The doctor command shows the files; it is not a fix, so the prompt names none.
    expect(row.prompt.command).toBeUndefined();
    // Skipped files do not undo a current index.
    expect(view.doneCount).toBe(2);
  });

  it("keeps the Not indexed row when an incomplete index also skipped files", () => {
    const s = setupFixture();
    const view = deriveSetup(
      { ...s, index: { ...s.index, state: "incomplete", parse_error_count: 1, parse_error_files: ["a.py"] } },
      OPTS,
    );
    expect(ids(view.attention)).toEqual(["index-parse"]);
    expect(view.essentials[0]!.status).toEqual({ key: "index.statusIncomplete" });
  });

  it("flags an index that is behind HEAD", () => {
    const s = setupFixture();
    const view = deriveSetup({ ...s, index: { ...s.index, state: "behind", live_head: "9999999aaa" } }, OPTS);
    expect(ids(view.attention)).toEqual(["index-behind"]);
    expect(view.attention[0]!.command).toBe("repowise update");
    expect(view.doneCount).toBe(1);
  });

  it("names the wrong-folder case for an agent with no call a day after it connected", () => {
    const s = setupFixture();
    const old = { ...s, index: { ...s.index, indexed_at: "2026-10-23T12:00:00Z" } };
    const view = deriveSetup(old, OPTS);
    expect(ids(view.attention)).toEqual(["agent-silent-claude-code"]);
    expect(view.attention[0]!.detail).toEqual({
      key: "agent.detailNoCalls",
      values: { agent: "Claude Code", repo: "orbit-api" },
    });
    expect(ids(view.essentials)).toEqual(["index", "sync"]);

    // Seen connected in this browser an hour ago: not a day yet.
    const recent = deriveSetup(old, {
      ...OPTS,
      agentSeenAt: { "claude-code": "2026-10-25T11:00:00Z" },
    });
    expect(recent.attention).toEqual([]);
  });

  it("turns doctor warnings and failures into attention rows, except the sync hook row", () => {
    const view = deriveSetup(
      setupFixture({
        checks: [
          { id: "post-commit-hook", name: "Post-commit hook", level: "warn", detail: "not installed" },
          { id: "vector-store", name: "Vector store", level: "fail", detail: "missing", fix: "repowise reindex" },
        ],
      }),
      OPTS,
    );
    expect(ids(view.attention)).toEqual(["check-vector-store"]);
    expect(view.attention[0]).toMatchObject({
      title: { text: "Vector store" },
      status: { key: "check.statusFail" },
      command: "repowise reindex",
    });
  });

  it("drops a closing (run X) from doctor text when it repeats the row command", () => {
    const view = deriveSetup(
      setupFixture({
        checks: [
          { id: "vector-store", name: "Vector store", level: "fail", detail: "missing (run: repowise reindex)", fix: "repowise reindex" },
          { id: "config", name: "Config", level: "warn", detail: "stale (run repowise doctor --fix)", fix: "repowise init" },
        ],
      }),
      OPTS,
    );
    expect(view.attention[0]!.detail).toEqual({ text: "missing" });
    expect(view.attention[0]!.prompt).toMatchObject({
      state: "missing",
      command: "repowise reindex",
      fromDoctor: true,
      fix: true,
    });
    // A different command in the hint is information, so it stays.
    expect(view.attention[1]!.detail).toEqual({ text: "stale (run repowise doctor --fix)" });
  });

  it("gives a doctor check with no fix a prompt with no command", () => {
    const view = deriveSetup(
      setupFixture({ checks: [{ id: "database", name: "Database", level: "fail", detail: "locked" }] }),
      OPTS,
    );
    const row = view.attention[0]!;
    expect(row.command).toBe("repowise doctor --format json");
    expect(row.prompt.command).toBeUndefined();
  });

  it("offers a discovered coverage report and the model sheet", () => {
    const [model, coverage, rewrite] = deriveSetup(setupFixture(), OPTS).optional;
    expect(model!.action).toEqual({ kind: "model", hasModel: false });
    expect(coverage!.tone).toBe("found");
    expect(coverage!.action).toEqual({ kind: "coverage", path: "coverage/lcov.info" });
    expect(coverage!.command).toBe("repowise coverage add coverage/lcov.info");
    expect(rewrite!.action).toEqual({ kind: "hook", hook: "rewrite", enabled: false, unavailable: false });
  });
});

describe("sections the server could not read", () => {
  const unreadable = (command: string) => ({ key: "unreadable.detail", values: { command } });
  const OLD_INDEX = { indexed_at: "2026-10-20T12:00:00Z" };

  it("calls_available false: no silent-agent row, and a registered agent is not failing", () => {
    const s = setupFixture({ calls_available: false });
    const view = deriveSetup({ ...s, index: { ...s.index, ...OLD_INDEX } }, OPTS);
    expect(view.attention).toEqual([]);
    const agent = view.essentials.find((r) => r.id === "agent")!;
    expect(agent.tone).toBe("done");
    expect(agent.detail).toEqual(unreadable("repowise status"));
    expect(view.doneCount).toBe(3);
  });

  it("agents_available false: the agent row says it couldn't read, not that none is connected", () => {
    const agent = deriveSetup(setupFixture({ agents_available: false, agents: [] }), OPTS).essentials[1]!;
    expect(agent.status).toEqual({ key: "unreadable.status" });
    expect(agent.detail).toEqual(unreadable("repowise agents"));
    expect(agent.action).toBeNull();
  });

  it("model_available false: the model row is not 'built from index'", () => {
    const model = deriveSetup(setupFixture({ model_available: false }), OPTS).optional[0]!;
    expect(model.status).toEqual({ key: "unreadable.status" });
    expect(model.detail).toEqual(unreadable("repowise status"));
    const llm = setupFixture({ model_available: false });
    const written = deriveSetup({ ...llm, model: { ...llm.model, docs_mode: "llm" } }, OPTS).optional[0]!;
    expect(written.status).toEqual({ key: "model.statusLlm" });
  });

  it("coverage_available false: no 'not found' state and no report action", () => {
    const coverage = deriveSetup(setupFixture({ coverage_available: false }), OPTS).optional[1]!;
    expect(coverage.status).toEqual({ key: "unreadable.status" });
    expect(coverage.detail).toEqual(unreadable("repowise coverage status"));
    expect(coverage.action).toBeNull();
  });

  it("checks_available false: flags the empty attention list as unknown", () => {
    expect(deriveSetup(setupFixture({ checks_available: false, checks: [] }), OPTS).checksUnreadable).toBe(true);
    expect(deriveSetup(setupFixture(), OPTS).checksUnreadable).toBe(false);
  });

  it("settings agent rows follow the same flags", () => {
    expect(agentListRows(setupFixture({ agents_available: false, agents: [] }), OPTS).map((r) => r.id)).toEqual([
      "agents-unreadable",
    ]);
    const rows = agentListRows(setupFixture({ calls_available: false }), OPTS);
    expect(rows[0]!.detail).toEqual(unreadable("repowise status"));
  });
});

describe("agentListRows", () => {
  it("lists connected, then installed, then the agents not installed in one row", () => {
    const rows = agentListRows(setupFixture(), OPTS);
    expect(ids(rows)).toEqual(["agent-claude-code", "agent-cursor", "agents-absent"]);
    expect(rows[0]!.action).toMatchObject({ kind: "disconnect", agentId: "claude-code" });
    expect(rows[1]!.detail).toEqual({
      key: "agents.detailInstalledWrites",
      values: { path: ".cursor/mcp.json" },
    });
    expect(rows[2]!.title).toEqual({ text: "Codex" });
  });
});
