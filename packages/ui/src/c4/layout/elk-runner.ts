/**
 * Runs an ELK layout off the main thread when it can.
 *
 * `worker: true` sends the graph to one shared Web Worker running
 * `elkjs/lib/elk-worker.min.js`, so a layered layout of a hundred cards no
 * longer blocks input. When a worker cannot start (no `Worker`, as in jsdom or
 * on the server; a blocked or failed script load) the same graph is laid out by
 * the bundled main-thread ELK, which is loaded on first use only. A worker that
 * fails once is not retried for the rest of the session.
 *
 * The worker URL is written as `new Worker(new URL(..., import.meta.url))`,
 * the form webpack 5, Turbopack and Vite all bundle as a worker entry.
 */

import ElkApi, { type ElkNode } from "elkjs/lib/elk-api.js";

interface ElkEngine {
  layout(graph: ElkNode): Promise<ElkNode>;
}

/** Raised when the worker script fails to load or crashes, as opposed to a layout error. */
class WorkerUnavailable extends Error {}

interface WorkerEngine {
  elk: ElkEngine;
  /** Rejects with `WorkerUnavailable` once the worker reports an error event. */
  failed: Promise<never>;
  terminate(): void;
}

/** `undefined`: not tried yet. `null`: this environment cannot run the worker. */
let workerEngine: WorkerEngine | null | undefined;
let mainThreadEngine: Promise<ElkEngine> | null = null;

function mainThreadElk(): Promise<ElkEngine> {
  mainThreadEngine ??= import("elkjs/lib/elk.bundled.js").then((m) => new m.default());
  return mainThreadEngine;
}

function startWorker(): WorkerEngine | null {
  if (workerEngine !== undefined) return workerEngine;
  workerEngine = null;
  if (typeof Worker === "undefined") return null;
  try {
    let fail: (reason: unknown) => void = () => {};
    const failed = new Promise<never>((_, reject) => {
      fail = reject;
    });
    // Observed through `Promise.race` only; never an unhandled rejection.
    failed.catch(() => {});
    let raw: Worker | null = null;
    const elk = new ElkApi({
      workerFactory: () => {
        raw = new Worker(new URL("elkjs/lib/elk-worker.min.js", import.meta.url));
        raw.addEventListener("error", () => fail(new WorkerUnavailable("ELK worker failed")));
        return raw;
      },
    });
    workerEngine = { elk, failed, terminate: () => raw?.terminate() };
  } catch {
    workerEngine = null;
  }
  return workerEngine;
}

/**
 * Lay out an ELK graph, in the worker when `worker` is set and one can run,
 * else on the main thread. A layout error from ELK itself is rethrown either way.
 */
export async function runElkLayout(graph: ElkNode, options: { worker?: boolean } = {}): Promise<ElkNode> {
  const engine = options.worker ? startWorker() : null;
  if (engine) {
    const laid = await layoutInWorker(engine, graph);
    if (laid) return laid;
  }
  return (await mainThreadElk()).layout(graph);
}

/** The worker's layout, or null when the worker failed and has been dropped. */
async function layoutInWorker(engine: WorkerEngine, graph: ElkNode): Promise<ElkNode | null> {
  try {
    return await Promise.race([engine.elk.layout(graph), engine.failed]);
  } catch (err) {
    if (!(err instanceof WorkerUnavailable)) throw err;
    engine.terminate();
    if (workerEngine === engine) workerEngine = null;
    return null;
  }
}

/** Test seam: forget the worker so the next layout tries to start one again. */
export function resetElkWorkerForTests(): void {
  workerEngine?.terminate();
  workerEngine = undefined;
}
