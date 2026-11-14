import { afterEach, describe, expect, it, vi } from "vitest";
import { createRequire } from "node:module";
import { resetElkWorkerForTests, runElkLayout } from "../../src/c4/layout/elk-runner";

// The worker script's in-process twin: the same message protocol the real
// worker speaks, without a thread. jsdom has no Worker, so the tests stub one.
const { Worker: InProcessElk } = createRequire(import.meta.url)("elkjs/lib/elk-worker.min.js") as {
  Worker: new () => { postMessage(msg: unknown): void; onmessage: ((e: { data: unknown }) => void) | null };
};

const graph = () => ({
  id: "root",
  layoutOptions: { "elk.algorithm": "layered" },
  children: [
    { id: "a", width: 10, height: 10 },
    { id: "b", width: 10, height: 10 },
  ],
  edges: [{ id: "e", sources: ["a"], targets: ["b"] }],
});

afterEach(() => {
  resetElkWorkerForTests();
  vi.unstubAllGlobals();
});

describe("runElkLayout", () => {
  it("lays out on the main thread when no Worker exists", async () => {
    const laid = await runElkLayout(graph(), { worker: true });
    expect(laid.children?.map((c) => c.id)).toEqual(["a", "b"]);
  });

  it("sends the layout to one shared worker built from the ELK worker script", async () => {
    const urls: string[] = [];
    const posted: string[] = [];
    class StubWorker extends EventTarget {
      onmessage: ((e: { data: unknown }) => void) | null = null;
      private elk = new InProcessElk();
      constructor(url: URL | string) {
        super();
        urls.push(String(url));
        this.elk.onmessage = (e) => this.onmessage?.(e);
      }
      postMessage(msg: { cmd: string }) {
        posted.push(msg.cmd);
        this.elk.postMessage(msg);
      }
      terminate() {}
    }
    vi.stubGlobal("Worker", StubWorker);
    const first = await runElkLayout(graph(), { worker: true });
    await runElkLayout(graph(), { worker: true });
    expect(urls).toHaveLength(1);
    expect(urls[0]).toContain("elk-worker.min.js");
    expect(posted.filter((c) => c === "layout")).toHaveLength(2);
    expect(first.children?.find((c) => c.id === "b")?.y).toBeGreaterThan(0);
  });

  it("falls back to the main thread when the worker fails to load, and does not retry it", async () => {
    let started = 0;
    class BrokenWorker extends EventTarget {
      onmessage = null;
      constructor() {
        super();
        started += 1;
        setTimeout(() => this.dispatchEvent(new Event("error")), 0);
      }
      postMessage() {}
      terminate() {}
    }
    vi.stubGlobal("Worker", BrokenWorker);
    const laid = await runElkLayout(graph(), { worker: true });
    expect(laid.children).toHaveLength(2);
    await runElkLayout(graph(), { worker: true });
    expect(started).toBe(1);
  });

  it("falls back when the Worker constructor throws", async () => {
    vi.stubGlobal(
      "Worker",
      class {
        constructor() {
          throw new Error("blocked by CSP");
        }
      },
    );
    const laid = await runElkLayout(graph(), { worker: true });
    expect(laid.children).toHaveLength(2);
  });

  it("stays on the main thread unless asked for the worker", async () => {
    const ctor = vi.fn();
    vi.stubGlobal("Worker", ctor);
    await runElkLayout(graph());
    expect(ctor).not.toHaveBeenCalled();
  });
});
