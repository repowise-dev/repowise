/**
 * Shared bootstrap for every webview entry: theme sync, host handshake,
 * loading state, error boundary, and re-init when the host pushes new params
 * into an already-open panel.
 */

import { Component, StrictMode, useEffect, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import type {
  InitMessage,
  RepoInit,
  ViewParams,
  WebviewViewId,
} from "../../../src/shared/webviewMessages";
import { createHost, type WebviewHost } from "./rpc";
import { initTheme, setThemePreference } from "./theme";

/** Everything a view component receives. */
export interface ViewProps<V extends WebviewViewId> {
  host: WebviewHost;
  repo: RepoInit;
  params: ViewParams[V];
  /** Increments when the index moves; refetch effects key on it. */
  refreshToken: number;
}

export function mountView<V extends WebviewViewId>(
  view: V,
  App: React.ComponentType<ViewProps<V>>,
): void {
  initTheme();
  const host = createHost();
  // Registered before render so the persisted preference is applied by the
  // time the view component first mounts (init subscribers fire in order).
  host.onInit((msg) => setThemePreference(msg.theme ?? "auto"));
  host.onThemeChanged((theme) => setThemePreference(theme));
  const container = document.getElementById("root");
  if (!container) throw new Error("Webview root element missing");
  createRoot(container).render(
    <StrictMode>
      <Bootstrap view={view} host={host} App={App} />
    </StrictMode>,
  );
  host.ready();
}

interface BootstrapProps<V extends WebviewViewId> {
  view: V;
  host: WebviewHost;
  App: React.ComponentType<ViewProps<V>>;
}

function Bootstrap<V extends WebviewViewId>({ view, host, App }: BootstrapProps<V>): ReactNode {
  const [init, setInit] = useState<InitMessage | null>(null);
  const [initSeq, setInitSeq] = useState(0);
  const [repo, setRepo] = useState<RepoInit | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  useEffect(() => {
    const offInit = host.onInit((msg) => {
      setInit(msg);
      setRepo(msg.repo);
      // A repeat init (same panel, new params) resets the view subtree.
      setInitSeq((n) => n + 1);
    });
    const offRefresh = host.onRefresh((msg) => {
      setRepo(msg.repo);
      setRefreshToken((n) => n + 1);
    });
    return () => {
      offInit();
      offRefresh();
    };
  }, [host]);

  if (!init || !repo) {
    return (
      <div className="flex h-screen items-center justify-center text-[var(--color-text-tertiary)]">
        Loading…
      </div>
    );
  }

  return (
    <ErrorBoundary key={initSeq}>
      <App
        host={host}
        repo={repo}
        params={init.params as ViewParams[V]}
        refreshToken={refreshToken}
      />
    </ErrorBoundary>
  );
}

interface ErrorBoundaryState {
  error: Error | null;
}

class ErrorBoundary extends Component<{ children: ReactNode }, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="m-6 rounded-lg border border-[var(--color-error)] p-4 text-sm">
          <p className="font-medium text-[var(--color-error)]">This view hit an error.</p>
          <p className="mt-2 text-[var(--color-text-secondary)]">{this.state.error.message}</p>
        </div>
      );
    }
    return this.props.children;
  }
}
