/**
 * Decisions panel: the web decisions page and its record page, composed from
 * the same shared components, with the record opening in place.
 *
 * The shared components link with web routes (`/repos/<id>/decisions/<id>`
 * and friends). There are no routes here, so one document-level listener maps
 * those hrefs onto the panel's own state: a record id opens the record, the
 * decisions root returns to the board, code health opens the Health panel,
 * and anything else is held inert rather than navigating the webview away.
 * Document level because the review drawer and dialogs render in portals.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTheme } from "next-themes";
import { Toaster } from "@repowise-dev/ui/shared/toast";
import type { ViewProps } from "../../runtime/mount";
import { useFileLinks } from "../../runtime/fileLinks";
import { DecisionsPage, type BoardContext } from "./Board";
import { DecisionRecordView } from "./Record";

type Route = { kind: "board" } | { kind: "record"; id: string };

/** What a web href means in the panel, or null when it is not a repo route. */
export function routeForHref(
  href: string,
  repoId: string,
): Route | { kind: "health" } | { kind: "inert" } | null {
  if (!href.startsWith("/repos/")) return null;
  const prefix = `/repos/${repoId}/decisions`;
  const path = href.split(/[?#]/)[0] ?? "";
  if (path === prefix || path === `${prefix}/`) return { kind: "board" };
  if (path.startsWith(`${prefix}/`)) {
    return { kind: "record", id: decodeURIComponent(path.slice(prefix.length + 1)) };
  }
  if (path.startsWith(`/repos/${repoId}/code-health`)) return { kind: "health" };
  return { kind: "inert" };
}

/** A plain anchor: the document listener owns what a click on it does. */
function PanelLink({
  href,
  className,
  children,
  ...rest
}: {
  href: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <a href={href} className={className} {...rest}>
      {children}
    </a>
  );
}

/** Keyed on the repository, so a reconnect to another one starts on its board. */
export function App(props: ViewProps<"decisions">) {
  return <DecisionsPanel key={props.repo.id} {...props} />;
}

function DecisionsPanel({ host, repo, refreshToken }: ViewProps<"decisions">) {
  const [route, setRoute] = useState<Route>({ kind: "board" });
  const [version, setVersion] = useState(0);
  const { resolvedTheme } = useTheme();
  useFileLinks(host);

  const refresh = useCallback(() => setVersion((v) => v + 1), []);

  // The panel scrolls in the chrome's container; each route opens at its top.
  const rootRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const scroller = rootRef.current?.parentElement;
    if (scroller) scroller.scrollTop = 0;
  }, [route]);

  // Capture phase: a row menu stops its clicks from bubbling, and its
  // "open the full record" item is an anchor with a web route.
  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0) return;
      const anchor = (event.target as Element | null)?.closest?.("a");
      const next = routeForHref(anchor?.getAttribute("href") ?? "", repo.id);
      if (!next) return;
      event.preventDefault();
      if (next.kind === "health") host.openView("health");
      else if (next.kind !== "inert") setRoute(next);
    };
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, [host, repo.id]);

  const ctx = useMemo<BoardContext>(
    () => ({
      host,
      repoId: repo.id,
      epoch: `${refreshToken}.${version}`,
      refresh,
      LinkComponent: PanelLink,
    }),
    [host, repo.id, refreshToken, version, refresh],
  );

  // The board unmounts under a record, as the web page does on navigation:
  // its review drawer renders in a portal, and a hidden board would leave an
  // open drawer over the record.
  return (
    <div ref={rootRef}>
      {route.kind === "record" ? (
        <DecisionRecordView ctx={ctx} decisionId={route.id} />
      ) : (
        <DecisionsPage ctx={ctx} />
      )}
      <Toaster theme={resolvedTheme === "dark" ? "dark" : "light"} />
    </div>
  );
}
