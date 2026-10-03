import { describe, expect, it } from "vitest";

import { isServeLock, type ServeLock } from "../src/serve-lock.js";

const VALID: ServeLock = {
  pid: 4242,
  host: "0.0.0.0",
  port: 7337,
  url: "http://127.0.0.1:7337",
  ui_port: 3000,
  server_version: "0.56.0",
  started_at: "2026-10-03T10:00:00Z",
};

describe("isServeLock", () => {
  it("accepts a complete lockfile", () => {
    expect(isServeLock(VALID)).toBe(true);
  });

  it("accepts a null ui_port (server started without the web UI)", () => {
    expect(isServeLock({ ...VALID, ui_port: null })).toBe(true);
  });

  it("rejects a lockfile missing any field", () => {
    for (const key of Object.keys(VALID)) {
      const partial: Record<string, unknown> = { ...VALID };
      delete partial[key];
      expect(isServeLock(partial), key).toBe(false);
    }
  });

  it("rejects fields of the wrong type", () => {
    expect(isServeLock({ ...VALID, pid: "4242" })).toBe(false);
    expect(isServeLock({ ...VALID, host: 1 })).toBe(false);
    expect(isServeLock({ ...VALID, port: "7337" })).toBe(false);
    expect(isServeLock({ ...VALID, url: null })).toBe(false);
    expect(isServeLock({ ...VALID, ui_port: "3000" })).toBe(false);
    expect(isServeLock({ ...VALID, ui_port: undefined })).toBe(false);
    expect(isServeLock({ ...VALID, server_version: 56 })).toBe(false);
    expect(isServeLock({ ...VALID, started_at: 0 })).toBe(false);
  });

  it("rejects non-object garbage", () => {
    for (const value of [null, undefined, 0, "lock", true, [], [VALID]]) {
      expect(isServeLock(value)).toBe(false);
    }
  });
});
