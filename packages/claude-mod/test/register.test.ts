import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Hook, ModApi, On } from "../src/mod-api";

type Hooks = Record<string, Hook<any>>;

async function load(): Promise<Hooks> {
  vi.resetModules();
  const { register } = await import("../src/register");
  const hooks: Hooks = {};
  const on = ((event: string, a: unknown, b?: unknown) => {
    hooks[event] = (b ?? a) as Hook<any>;
  }) as On;
  register(on);
  return hooks;
}

/** A `$` for a git work tree with no index and a connecting MCP server. */
function fakeDollar() {
  const calls = { cwd: 0, invalidate: 0, logs: [] as string[] };
  let release: () => void = () => {};
  const gate = { hold: false, wait: Promise.resolve() };
  const $: ModApi = {
    session: {
      cwd: async () => {
        calls.cwd++;
        if (gate.hold) await gate.wait;
        return "/work/app";
      },
    },
    fs: { read: async () => Promise.reject(new Error("ENOENT")), exists: async () => false },
    process: {
      run: async (argv) => ({ exitCode: 0, stdout: argv.includes("--is-inside-work-tree") ? "true\n" : "", stderr: "" }),
    },
    http: { fetch: async () => ({ status: 500, ok: false, headers: {}, text: "" }) },
    mcp: { connect: async () => ({ isConnected: true }) },
    ui: {
      invalidate: () => {
        calls.invalidate++;
      },
      log: (text) => {
        calls.logs.push(text);
      },
      resolve: () => ({
        Box: (props) => ({ el: "Box", props }),
        Text: (props) => ({ el: "Text", props }),
      }),
    },
  };
  const hold = () => {
    gate.hold = true;
    gate.wait = new Promise<void>((resolve) => (release = resolve));
  };
  return { $, calls, hold, release: () => release() };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const band = { props: { hasSurvey: false, bodyColumns: 100 } };
const THEIRS = { el: "theirs" };

let hooks: Hooks;
beforeEach(async () => {
  hooks = await load();
});

describe("register", () => {
  it("registers the three observing hooks and nothing else", () => {
    expect(Object.keys(hooks).sort()).toEqual(["session.start", "turn.complete", "ui.render"]);
  });

  it("every hook returns what next(e) returned", async () => {
    const { $ } = fakeDollar();
    const marker = { from: "next" };
    expect(await hooks["session.start"]!($, {}, async () => marker)).toBe(marker);
    expect(await hooks["turn.complete"]!($, {}, async () => marker)).toBe(marker);
    expect(await hooks["turn.complete"]!($, { agentId: "a" }, async () => marker)).toBe(marker);
  });

  it("the band passes the site on at rest", async () => {
    const { $ } = fakeDollar();
    expect(await hooks["ui.render"]!($, band, async () => THEIRS)).toBe(THEIRS);
  });

  it("draws its rows above whatever the next hook drew, and alone when nothing was", async () => {
    const { $, calls } = fakeDollar();
    await hooks["session.start"]!($, {}, async () => undefined);
    await settle();
    expect(calls.invalidate).toBe(1);
    const composed = (await hooks["ui.render"]!($, band, async () => THEIRS)) as { props: { children: unknown[] } };
    expect(composed.props.children[1]).toBe(THEIRS);
    expect(JSON.stringify(composed.props.children[0])).toContain("index this repo for Lens");
    const alone = await hooks["ui.render"]!($, band, async () => null);
    expect(JSON.stringify(alone)).toContain("index this repo for Lens");
  });

  it("a failing draw keeps the band as it was and says why in the debug log only", async () => {
    const { $, calls } = fakeDollar();
    await hooks["session.start"]!($, {}, async () => undefined);
    await settle();
    $.ui.resolve = () => {
      throw new Error("no table");
    };
    expect(await hooks["ui.render"]!($, band, async () => THEIRS)).toBe(THEIRS);
    expect(calls.logs).toEqual(["lens: band render failed: Error: no table"]);
  });

  it("a failing refresh is logged, not thrown", async () => {
    const { $, calls } = fakeDollar();
    $.session.cwd = async () => {
      throw new Error("no cwd");
    };
    await hooks["session.start"]!($, {}, async () => undefined);
    await settle();
    expect(calls.logs).toEqual(["lens: refresh failed: Error: no cwd"]);
    expect(calls.invalidate).toBe(0);
  });

  it("yields to a survey", async () => {
    const { $ } = fakeDollar();
    await hooks["session.start"]!($, {}, async () => undefined);
    await settle();
    expect(await hooks["ui.render"]!($, { props: { hasSurvey: true } }, async () => THEIRS)).toBe(THEIRS);
  });

  it("discovers on the first turn when the module loaded after the session started", async () => {
    const { $, calls } = fakeDollar();
    await hooks["turn.complete"]!($, {}, async () => undefined);
    await settle();
    expect(calls.cwd).toBe(1);
  });

  it("runs once more after a refresh asked for while one was running", async () => {
    const { $, calls, hold, release } = fakeDollar();
    hold();
    await hooks["session.start"]!($, {}, async () => undefined);
    await hooks["turn.complete"]!($, {}, async () => undefined);
    await hooks["turn.complete"]!($, {}, async () => undefined);
    expect(calls.cwd).toBe(1);
    release();
    await settle();
    await settle();
    expect(calls.cwd).toBe(2);
  });

  it("drops a refresh that finishes after a new session started", async () => {
    const old = fakeDollar();
    old.hold();
    await hooks["session.start"]!(old.$, {}, async () => undefined);
    const fresh = fakeDollar();
    await hooks["session.start"]!(fresh.$, {}, async () => undefined);
    old.release();
    await settle();
    await settle();
    // The old run lands nothing; the new session's own refresh then draws.
    expect(old.calls.invalidate).toBe(0);
    expect(fresh.calls.invalidate).toBe(1);
  });
});
