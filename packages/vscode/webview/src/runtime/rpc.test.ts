/**
 * Round-trip test of the message contract: a scripted "host" answers the
 * webview client through the same envelopes core/webviews.ts uses, so a
 * protocol drift on either side fails here before it fails in the editor.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  HostToWebviewMessage,
  InitMessage,
  WebviewToHostMessage,
} from "../../../src/shared/webviewMessages";

const INIT: InitMessage = {
  kind: "init",
  view: "health",
  repo: { id: "r1", name: "repo", headCommit: "abc", defaultBranch: "main" },
  params: {},
  theme: "auto",
};

function postToWebview(msg: HostToWebviewMessage): void {
  window.dispatchEvent(new MessageEvent("message", { data: msg }));
}

/** Installs acquireVsCodeApi with a scripted host on the other side. */
function installHost() {
  const received: WebviewToHostMessage[] = [];
  (globalThis as Record<string, unknown>).acquireVsCodeApi = () => ({
    postMessage(msg: WebviewToHostMessage) {
      received.push(msg);
      if (msg.kind === "ready") {
        postToWebview(INIT);
      } else if (msg.kind === "rpc-request") {
        if (msg.method === "healthTrend") {
          postToWebview({ kind: "rpc-response", id: msg.id, ok: true, result: { history: [] } });
        } else {
          postToWebview({ kind: "rpc-response", id: msg.id, ok: false, error: "Unknown method" });
        }
      }
    },
    getState: () => undefined,
    setState: () => {},
  });
  return received;
}

describe("webview rpc round trip", () => {
  beforeEach(() => {
    // The singleton caches acquireVsCodeApi's result; a fresh module registry
    // per test keeps hosts isolated.
    vi.resetModules();
  });

  afterEach(() => {
    delete (globalThis as Record<string, unknown>).acquireVsCodeApi;
  });

  it("delivers init to subscribers, including late ones", async () => {
    installHost();
    const { createHost } = await import("./rpc");
    const host = createHost();

    const early: InitMessage[] = [];
    host.onInit((msg) => early.push(msg));
    host.ready();
    expect(early).toHaveLength(1);
    expect(early[0]?.repo.id).toBe("r1");

    // A subscriber attaching after the reply still receives the buffered init.
    const late: InitMessage[] = [];
    host.onInit((msg) => late.push(msg));
    expect(late).toHaveLength(1);
  });

  it("resolves rpc calls and rejects unknown methods with the host error", async () => {
    installHost();
    const { createHost } = await import("./rpc");
    const host = createHost();

    await expect(host.api.healthTrend(5)).resolves.toEqual({ history: [] });
    await expect(host.api.decisionsList()).rejects.toThrow("Unknown method");
  });

  it("routes one-way services with their payloads", async () => {
    const received = installHost();
    const { createHost } = await import("./rpc");
    const host = createHost();

    host.openFile("src/a.py", 12);
    host.copyText("hello", "Copied.");
    host.openExternal("https://example.com");

    expect(received).toContainEqual({ kind: "open-file", path: "src/a.py", line: 12 });
    expect(received).toContainEqual({ kind: "copy-text", text: "hello", toast: "Copied." });
    expect(received).toContainEqual({ kind: "open-external", url: "https://example.com" });
  });

  it("routes home-view services and delivers update-done", async () => {
    const received = installHost();
    const { createHost } = await import("./rpc");
    const host = createHost();

    host.openView("health");
    host.openView("refactoring", { planId: "p1" });
    host.updateIndex();
    host.setTheme("dark");
    expect(received).toContainEqual({ kind: "open-view", view: "health", params: undefined });
    expect(received).toContainEqual({ kind: "open-view", view: "refactoring", params: { planId: "p1" } });
    expect(received).toContainEqual({ kind: "update-index" });
    expect(received).toContainEqual({ kind: "set-theme", theme: "dark" });

    const done = vi.fn();
    host.onUpdateDone(done);
    postToWebview({ kind: "update-done" });
    expect(done).toHaveBeenCalledTimes(1);

    const themed = vi.fn();
    host.onThemeChanged(themed);
    postToWebview({ kind: "theme-changed", theme: "light" });
    expect(themed).toHaveBeenCalledWith("light");
  });

  it("ignores garbage messages without breaking pending calls", async () => {
    installHost();
    const { createHost } = await import("./rpc");
    const host = createHost();

    postToWebview(null as unknown as HostToWebviewMessage);
    postToWebview({ kind: "rpc-response", id: 999, ok: true } as HostToWebviewMessage);

    await expect(host.api.healthTrend()).resolves.toEqual({ history: [] });
  });
});
