/**
 * Webview side of the message contract. Webviews never fetch: `host.api` is a
 * Proxy that turns every HostApi call into an rpc-request the extension host
 * serves from its shared cache and api-client.
 */

import type {
  HostApi,
  HostApiMethod,
  HostToWebviewMessage,
  InitMessage,
  RefreshMessage,
} from "../../../src/shared/webviewMessages";
import { getVsCodeApi } from "./vscodeApi";

export interface WebviewHost {
  /** Typed data calls served by the extension host. */
  api: HostApi;
  /** Fires once per init push (first load and any re-open with new params). */
  onInit(cb: (msg: InitMessage) => void): () => void;
  /** Fires when the index moved and the view should refetch. */
  onRefresh(cb: (msg: RefreshMessage) => void): () => void;
  /** Tell the host we are listening; it answers with init. */
  ready(): void;
  openFile(path: string, line?: number): void;
  copyText(text: string, toast?: string): void;
  openExternal(url: string): void;
}

interface Pending {
  resolve: (value: unknown) => void;
  reject: (err: Error) => void;
}

export function createHost(): WebviewHost {
  const vscode = getVsCodeApi();
  const pending = new Map<number, Pending>();
  const initSubs = new Set<(msg: InitMessage) => void>();
  const refreshSubs = new Set<(msg: RefreshMessage) => void>();
  // Buffered so a subscriber attaching after the host already replied to
  // ready() (possible if React yields before the effect commits) still gets it.
  let lastInit: InitMessage | null = null;
  let nextId = 1;

  window.addEventListener("message", (event: MessageEvent<HostToWebviewMessage>) => {
    const msg = event.data;
    if (!msg || typeof msg !== "object") return;
    switch (msg.kind) {
      case "rpc-response": {
        const entry = pending.get(msg.id);
        if (!entry) return;
        pending.delete(msg.id);
        if (msg.ok) entry.resolve(msg.result);
        else entry.reject(new Error(msg.error ?? "Request failed."));
        return;
      }
      case "init":
        lastInit = msg;
        for (const cb of initSubs) cb(msg);
        return;
      case "refresh":
        for (const cb of refreshSubs) cb(msg);
        return;
    }
  });

  const api = new Proxy({} as HostApi, {
    get(_target, prop: string) {
      return (...args: unknown[]) =>
        new Promise((resolve, reject) => {
          const id = nextId++;
          pending.set(id, { resolve, reject });
          vscode.postMessage({
            kind: "rpc-request",
            id,
            method: prop as HostApiMethod,
            args,
          });
        });
    },
  });

  return {
    api,
    onInit(cb) {
      initSubs.add(cb);
      if (lastInit) cb(lastInit);
      return () => initSubs.delete(cb);
    },
    onRefresh(cb) {
      refreshSubs.add(cb);
      return () => refreshSubs.delete(cb);
    },
    ready: () => vscode.postMessage({ kind: "ready" }),
    openFile: (path, line) => vscode.postMessage({ kind: "open-file", path, line }),
    copyText: (text, toast) => vscode.postMessage({ kind: "copy-text", text, toast }),
    openExternal: (url) => vscode.postMessage({ kind: "open-external", url }),
  };
}
