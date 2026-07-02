import * as vscode from "vscode";
import * as path from "node:path";
import { randomBytes } from "node:crypto";
import { Commands, Views } from "../constants";
import type { RepowiseContext } from "./context";
import { createHostApi } from "./webviewApi";
import type {
  HostApi,
  HostToWebviewMessage,
  PanelViewId,
  RepoInit,
  ThemePreference,
  ViewParams,
  WebviewToHostMessage,
  WebviewViewId,
} from "../shared/webviewMessages";

/** Workspace-state key for the persisted webview theme preference. */
const THEME_STATE_KEY = "repowise.webviewTheme";

const THEME_VALUES: readonly ThemePreference[] = ["auto", "light", "dark"];

interface ViewMeta {
  title: string;
  /**
   * Keep the DOM alive while the tab is hidden. Off by default: panels are
   * cheap to rebuild from the host cache. At most one heavy graph view earns
   * this, decided from measurements, not taste.
   */
  retainContextWhenHidden: boolean;
}

const VIEW_META: Record<PanelViewId, ViewMeta> = {
  health: { title: "Repowise Health", retainContextWhenHidden: false },
  architecture: { title: "Repowise Architecture", retainContextWhenHidden: false },
  graph: { title: "Repowise Knowledge Graph", retainContextWhenHidden: false },
  refactoring: { title: "Repowise Refactoring Plan", retainContextWhenHidden: false },
  decisions: { title: "Repowise Decisions", retainContextWhenHidden: false },
  docs: { title: "Repowise Docs", retainContextWhenHidden: false },
  risk: { title: "Repowise Branch Risk", retainContextWhenHidden: false },
};

/** Tab title for a panel; the sidebar Home view's title lives in the manifest. */
function viewTitle(view: WebviewViewId): string {
  return view === "home" ? "Repowise" : VIEW_META[view].title;
}

/** Singleton wired by registerWebviews(); features open panels through it. */
let manager: WebviewManager | null = null;

/**
 * Opens (or reveals) the panel for a view. When the panel is already open,
 * fresh params are pushed as a new init message so e.g. a different
 * refactoring plan replaces the current one instead of spawning a second tab.
 */
export function openViewPanel<V extends PanelViewId>(
  ctx: RepowiseContext,
  view: V,
  params?: ViewParams[V],
): void {
  if (ctx.getExtensionState() !== "ready" || !ctx.repoId) {
    void vscode.window.showWarningMessage(
      "Connect to the Repowise server to open this view.",
    );
    return;
  }
  if (!manager) {
    ctx.log.error("openViewPanel called before registerWebviews");
    return;
  }
  manager.open(view, params ?? ({} as ViewParams[V]));
}

/**
 * Registers the panel infrastructure and the sidebar Home webview view. Idle
 * until the first panel open or until the sidebar resolves the Home view.
 */
export function registerWebviews(
  ctx: RepowiseContext,
  extensionUri: vscode.Uri,
): vscode.Disposable {
  const created = new WebviewManager(ctx, extensionUri);
  manager = created;
  const homeProvider = vscode.window.registerWebviewViewProvider(Views.homeDashboard, {
    resolveWebviewView: (view) => created.resolveHome(view),
  });
  return {
    dispose(): void {
      homeProvider.dispose();
      manager?.dispose();
      manager = null;
    },
  };
}

class WebviewManager {
  private readonly panels = new Map<PanelViewId, vscode.WebviewPanel>();
  private readonly params = new Map<PanelViewId, unknown>();
  /** The sidebar Home view, while resolved. */
  private homeView: vscode.WebviewView | null = null;
  private readonly hostApi: HostApi;
  private freshnessSub: vscode.Disposable | null = null;
  private readonly disposables: vscode.Disposable[] = [];
  /**
   * Folded into the host cache tag so every refresh broadcast forces fresh
   * fetches even when the server reports an unchanged (or null) head commit.
   */
  private epoch = 0;

  constructor(
    private readonly ctx: RepowiseContext,
    private readonly extensionUri: vscode.Uri,
  ) {
    this.hostApi = createHostApi(ctx, () => this.epoch);
    // A reconnect can resolve a different repo (or a moved index) without an
    // indexChanged event; re-init open panels so they never mix repos.
    this.disposables.push(
      ctx.onDidChangeExtensionState((state) => {
        if (state !== "ready") return;
        this.epoch += 1;
        this.broadcastRefresh();
      }),
    );
  }

  private broadcastRefresh(): void {
    const repo = this.repoInit();
    const message = { kind: "refresh", repo } satisfies HostToWebviewMessage;
    for (const panel of this.panels.values()) {
      void panel.webview.postMessage(message);
    }
    if (this.homeView) void this.homeView.webview.postMessage(message);
  }

  /**
   * Wires the sidebar Home webview view. VS Code resolves it when the view
   * first becomes visible; with context retention off the webview content is
   * torn down while hidden and re-runs its script (a fresh ready/init
   * handshake) on every reveal, so no visibility handling is needed here.
   */
  resolveHome(view: vscode.WebviewView): void {
    this.homeView = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "dist", "webview")],
    };
    view.webview.html = this.renderHtml("home", view.webview);
    const msgSub = view.webview.onDidReceiveMessage((msg: WebviewToHostMessage) =>
      void this.onMessage("home", view.webview, msg),
    );
    const dispSub = view.onDidDispose(() => {
      if (this.homeView === view) this.homeView = null;
      msgSub.dispose();
      dispSub.dispose();
    });
    this.ensureFreshness();
  }

  open(view: PanelViewId, params: unknown): void {
    this.params.set(view, params);
    const existing = this.panels.get(view);
    if (existing) {
      existing.reveal(undefined, false);
      this.postInit(view, existing.webview);
      return;
    }

    const meta = VIEW_META[view];
    const panel = vscode.window.createWebviewPanel(
      `repowise.${view}`,
      viewTitle(view),
      vscode.ViewColumn.Active,
      {
        enableScripts: true,
        retainContextWhenHidden: meta.retainContextWhenHidden,
        localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "dist", "webview")],
      },
    );
    panel.iconPath = vscode.Uri.joinPath(this.extensionUri, "media", "repowise.svg");
    panel.webview.html = this.renderHtml(view, panel.webview);
    // Per-panel subscriptions are released with the panel, not the manager,
    // so open/close cycles never accumulate dead listeners.
    const msgSub = panel.webview.onDidReceiveMessage((msg: WebviewToHostMessage) =>
      void this.onMessage(view, panel.webview, msg),
    );
    const dispSub = panel.onDidDispose(() => {
      this.panels.delete(view);
      this.params.delete(view);
      msgSub.dispose();
      dispSub.dispose();
    });
    this.panels.set(view, panel);
    this.ensureFreshness();
  }

  /** One freshness subscription for all panels, created on first open. */
  private ensureFreshness(): void {
    if (this.freshnessSub) return;
    const events = this.ctx.events();
    if (!events) return;
    this.freshnessSub = events.onDidChange((kind) => {
      if (kind !== "indexChanged") return;
      this.epoch += 1;
      void this.ctx.refreshRepo().then(() => this.broadcastRefresh());
    });
  }

  private repoInit(): RepoInit {
    const repo = this.ctx.repo;
    return {
      id: repo?.id ?? "",
      name: repo?.name ?? path.basename(this.ctx.workspace.repoRoot ?? "repository"),
      headCommit: repo?.head_commit ?? null,
      defaultBranch: repo?.default_branch ?? null,
    };
  }

  private themePref(): ThemePreference {
    const stored = this.ctx.state.get<ThemePreference>(THEME_STATE_KEY);
    return stored && THEME_VALUES.includes(stored) ? stored : "auto";
  }

  private postInit(view: WebviewViewId, webview: vscode.Webview): void {
    const params = view === "home" ? {} : (this.params.get(view) ?? {});
    const message = {
      kind: "init",
      view,
      repo: this.repoInit(),
      params: params as ViewParams[typeof view],
      theme: this.themePref(),
    } satisfies HostToWebviewMessage;
    void webview.postMessage(message);
  }

  private async onMessage(
    view: WebviewViewId,
    webview: vscode.Webview,
    msg: WebviewToHostMessage,
  ): Promise<void> {
    try {
      await this.handleMessage(view, webview, msg);
    } catch (err) {
      // Webview payloads are untrusted input; a malformed one is logged, never
      // an unhandled rejection.
      this.ctx.log.warn(`webview ${view} message failed: ${String(err)}`);
    }
  }

  private async handleMessage(
    view: WebviewViewId,
    webview: vscode.Webview,
    msg: WebviewToHostMessage,
  ): Promise<void> {
    switch (msg.kind) {
      case "ready":
        this.postInit(view, webview);
        return;
      case "rpc-request": {
        let response: HostToWebviewMessage;
        try {
          // Own-property check keeps inherited Object.prototype members
          // (constructor, toString, ...) out of the dispatch surface.
          if (!Object.hasOwn(this.hostApi, msg.method)) {
            throw new Error(`Unknown method: ${String(msg.method)}`);
          }
          const method = this.hostApi[msg.method] as (...a: unknown[]) => Promise<unknown>;
          const result = await method.apply(this.hostApi, msg.args);
          response = { kind: "rpc-response", id: msg.id, ok: true, result };
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          this.ctx.log.warn(`webview ${view} ${String(msg.method)} failed: ${message}`);
          response = { kind: "rpc-response", id: msg.id, ok: false, error: message };
        }
        void webview.postMessage(response);
        return;
      }
      case "open-view": {
        // The view id is untrusted input from the webview; validate against
        // the panel table before opening anything.
        if (typeof msg.view !== "string" || !Object.hasOwn(VIEW_META, msg.view)) return;
        const params =
          msg.params && typeof msg.params === "object" ? msg.params : undefined;
        openViewPanel(this.ctx, msg.view, params);
        return;
      }
      case "update-index":
        // Resolves when the update finishes (the command returns its promise).
        // A successful update also lands as a refresh broadcast; this ack is
        // what lets the view stop its spinner when the update fails.
        await vscode.commands.executeCommand(Commands.updateIndex);
        void webview.postMessage({ kind: "update-done" } satisfies HostToWebviewMessage);
        return;
      case "set-theme": {
        if (!THEME_VALUES.includes(msg.theme)) return;
        await this.ctx.state.update(THEME_STATE_KEY, msg.theme);
        const message = {
          kind: "theme-changed",
          theme: msg.theme,
        } satisfies HostToWebviewMessage;
        for (const panel of this.panels.values()) {
          void panel.webview.postMessage(message);
        }
        if (this.homeView) void this.homeView.webview.postMessage(message);
        return;
      }
      case "open-file": {
        const root = this.ctx.workspace.repoRoot;
        if (!root || typeof msg.path !== "string") return;
        // Clamp to the workspace: a repo-relative path from a webview must
        // never escape the root.
        const abs = path.resolve(root, msg.path);
        if (abs !== path.resolve(root) && !abs.startsWith(path.resolve(root) + path.sep)) {
          this.ctx.log.warn(`webview ${view} tried to open a path outside the repo: ${msg.path}`);
          return;
        }
        const options: vscode.TextDocumentShowOptions = { preview: true };
        if (typeof msg.line === "number" && Number.isFinite(msg.line)) {
          options.selection = new vscode.Range(Math.max(0, msg.line - 1), 0, Math.max(0, msg.line - 1), 0);
        }
        try {
          await vscode.window.showTextDocument(vscode.Uri.file(abs), options);
        } catch {
          void vscode.window.showWarningMessage(`Could not open ${msg.path}.`);
        }
        return;
      }
      case "copy-text":
        if (typeof msg.text !== "string") return;
        await vscode.env.clipboard.writeText(msg.text);
        void vscode.window.showInformationMessage(
          typeof msg.toast === "string" ? msg.toast : "Copied to clipboard.",
        );
        return;
      case "open-external":
        if (typeof msg.url === "string" && /^https?:\/\//.test(msg.url)) {
          void vscode.env.openExternal(vscode.Uri.parse(msg.url));
        }
        return;
    }
  }

  private renderHtml(view: WebviewViewId, webview: vscode.Webview): string {
    const dist = vscode.Uri.joinPath(this.extensionUri, "dist", "webview");
    const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(dist, `${view}.js`));
    const styleUri = webview.asWebviewUri(vscode.Uri.joinPath(dist, "webview.css"));
    const nonce = createNonce();
    // script-src needs cspSource beyond the nonce: the entry module lazily
    // imports its chunks (shiki, mermaid, view code), and those loads are
    // validated against the source list, not the entry's nonce.
    const csp = [
      "default-src 'none'",
      `img-src ${webview.cspSource} data:`,
      `style-src ${webview.cspSource} 'unsafe-inline'`,
      `font-src ${webview.cspSource}`,
      `script-src 'nonce-${nonce}' ${webview.cspSource}`,
    ].join("; ");
    return [
      "<!DOCTYPE html>",
      `<html lang="en">`,
      "<head>",
      `<meta charset="UTF-8" />`,
      `<meta http-equiv="Content-Security-Policy" content="${csp}" />`,
      `<meta name="viewport" content="width=device-width, initial-scale=1.0" />`,
      `<link rel="stylesheet" href="${styleUri.toString()}" />`,
      `<title>${viewTitle(view)}</title>`,
      "</head>",
      "<body>",
      `<div id="root"></div>`,
      `<script type="module" nonce="${nonce}" src="${scriptUri.toString()}"></script>`,
      "</body>",
      "</html>",
    ].join("\n");
  }

  dispose(): void {
    this.freshnessSub?.dispose();
    this.freshnessSub = null;
    for (const panel of this.panels.values()) panel.dispose();
    this.panels.clear();
    // The sidebar view is owned by VS Code; dropping the reference is all the
    // cleanup available (its own onDidDispose releases the subscriptions).
    this.homeView = null;
    for (const d of this.disposables) d.dispose();
    this.disposables.length = 0;
  }
}

function createNonce(): string {
  return randomBytes(16).toString("base64");
}
