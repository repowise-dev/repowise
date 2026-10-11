/** Singleton wrapper over acquireVsCodeApi (callable exactly once per page). */

import type { WebviewToHostMessage } from "../../../src/shared/webviewMessages";

interface VsCodeApi {
  postMessage(message: WebviewToHostMessage): void;
  getState(): unknown;
  setState(state: unknown): void;
}

declare function acquireVsCodeApi(): VsCodeApi;

let instance: VsCodeApi | null = null;

export function getVsCodeApi(): VsCodeApi {
  if (!instance) instance = acquireVsCodeApi();
  return instance;
}
