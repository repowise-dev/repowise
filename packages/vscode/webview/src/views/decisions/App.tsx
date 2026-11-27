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

export function App({ host, repo, refreshToken }: ViewProps<"decisions">) {
  const [route, setRoute] = useState<Route>({ kind: "board" });
  const [version, setVersion] = useState(0);
  const { resolvedTheme } = useTheme();
  useFileLinks(host);

  const refresh = useCallback(() => setVersion((v) => v + 1), []);

  // The panel scrolls in the chrome's container, the board's parent. A record
  // opens at its top; back puts the board where the reader left it.
  const boardRef = useRef<HTMLDivElement>(null);
  const boardScroll = useRef(0);
  const openRecord = route.kind === "record" ? route.id : null;
  useLayoutEffect(() => {
    const scroller = boardRef.current?.parentElement;
    if (!scroller) return;
    scroller.scrollTop = openRecord ? 0 : boardScroll.current;
  }, [openRecord]);
  const go = useCallback((next: Route) => {
    const scroller = boardRef.current?.parentElement;
    if (scroller && !boardRef.current?.hidden) boardScroll.current = scroller.scrollTop;
    setRoute(next);
  }, []);

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0) return;
      const anchor = (event.target as Element | null)?.closest?.("a");
      const next = routeForHref(anchor?.getAttribute("href") ?? "", repo.id);
      if (!next) return;
      event.preventDefault();
      if (next.kind === "health") host.openView("health");
      else if (next.kind !== "inert") go(next);
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [host, repo.id, go]);

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

  return (
    <>
      {/* The board stays mounted under an open record, so back returns to the
          lane, page and filter the reader left. */}
      <div ref={boardRef} hidden={route.kind === "record"}>
        <DecisionsPage ctx={ctx} />
      </div>
      {route.kind === "record" && <DecisionRecordView ctx={ctx} decisionId={route.id} />}
      <Toaster theme={resolvedTheme === "dark" ? "dark" : "light"} />
    </>
  );
}
