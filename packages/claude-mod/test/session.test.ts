import { describe, expect, it } from "vitest";
import { fromTurnComplete, isPluginCall } from "../src/model/events";
import { hintFor, initialSession, reduce, type SessionAction, type SessionState } from "../src/model/session";

function run(actions: SessionAction[], from: SessionState = initialSession): SessionState {
  return actions.reduce(reduce, from);
}

const lite: SessionAction = { type: "discovered", mode: "lite", freshness: null };
const full: SessionAction = { type: "discovered", mode: "full", freshness: null };
const noIndex: SessionAction = { type: "discovered", mode: "no-index", freshness: null };
const noCli: SessionAction = { type: "discovered", mode: "no-cli", freshness: null };
const turn: SessionAction = { type: "turnCompleted" };

describe("hintFor", () => {
  it("names one hint per degraded mode and none in full mode", () => {
    expect(hintFor("full", undefined)).toBeNull();
    expect(hintFor("lite", undefined)).toBe("no-server");
    expect(hintFor("lite", "auth")).toBe("auth");
    expect(hintFor("lite", "unlisted")).toBe("unlisted");
    expect(hintFor("no-index", undefined)).toBe("no-index");
    expect(hintFor("no-cli", undefined)).toBe("no-cli");
  });
});

describe("session reducer", () => {
  it("starts with no mode and nothing to show", () => {
    expect(initialSession).toEqual({ mode: null, freshness: null, hint: null, hintsShown: [] });
  });

  it("full mode shows no hint", () => {
    expect(run([full])).toMatchObject({ mode: "full", hint: null, hintsShown: [] });
  });

  it.each([
    [lite, "no-server"],
    [{ ...lite, liteReason: "auth" } as SessionAction, "auth"],
    [noIndex, "no-index"],
    [noCli, "no-cli"],
  ])("a degraded mode raises its hint", (action, kind) => {
    const s = run([action]);
    expect(s.hint).toBe(kind);
    expect(s.hintsShown).toEqual([kind]);
  });

  it("a hint shows once per session: it retires after a turn and never comes back", () => {
    const afterTurn = run([noIndex, turn]);
    expect(afterTurn.hint).toBeNull();
    const again = run([noIndex, turn, noIndex, turn, noIndex]);
    expect(again.hint).toBeNull();
    expect(again.hintsShown).toEqual(["no-index"]);
  });

  it("rediscovering the same mode keeps the hint on screen until a turn ends", () => {
    expect(run([lite, lite]).hint).toBe("no-server");
  });

  it("lite to full: the map hint leaves at once", () => {
    const s = run([lite, full]);
    expect(s.mode).toBe("full");
    expect(s.hint).toBeNull();
  });

  it("full back to lite after the hint was shown does not show it again", () => {
    expect(run([lite, full, lite]).hint).toBeNull();
  });

  it("a new degraded state shows its own hint even after another one was shown", () => {
    const s = run([noCli, turn, noIndex]);
    expect(s.hint).toBe("no-index");
    expect(s.hintsShown).toEqual(["no-cli", "no-index"]);
  });

  it("no-index to lite (the user indexed): the old hint leaves and the lite hint shows", () => {
    const s = run([noIndex, lite]);
    expect(s.hint).toBe("no-server");
    expect(s.mode).toBe("lite");
  });

  it("keeps freshness only where an index and the CLI exist", () => {
    const behind = { changedFiles: 14 };
    expect(run([{ ...full, freshness: behind }]).freshness).toEqual(behind);
    expect(run([{ ...lite, freshness: behind }]).freshness).toEqual(behind);
    expect(run([{ ...noCli, freshness: behind }]).freshness).toBeNull();
    expect(run([{ ...noIndex, freshness: behind }]).freshness).toBeNull();
  });

  it("freshness clears when the index catches up", () => {
    expect(run([{ ...full, freshness: { changedFiles: 3 } }, full]).freshness).toBeNull();
  });

  it("a turn with no hint up changes nothing", () => {
    const s = run([full]);
    expect(reduce(s, turn)).toBe(s);
  });
});

describe("events", () => {
  it("recognizes only mod-made call ids", () => {
    expect(isPluginCall("toolu_plugin_01ab")).toBe(true);
    expect(isPluginCall("toolu_01ab")).toBe(false);
    expect(isPluginCall(undefined)).toBe(false);
  });

  it("counts only main-loop turns", () => {
    expect(fromTurnComplete({})).toEqual({ type: "turnCompleted" });
    expect(fromTurnComplete({ agentId: "a1" })).toBeNull();
  });
});
