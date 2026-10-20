"use client";

/**
 * What this field is, in one sentence, from the response that drew it, plus
 * the few actions that change it.
 *
 * A count and the visual it describes have to come from one source, so this
 * reads the server's own selection totals rather than the array length: a
 * caption saying "3,808 files" over a two-thousand node field is a claim the
 * reader has no way to check. The actions sit here because this is where the
 * reader learns the field is partial.
 */

import type { ReactNode } from "react";
import type { MapScope } from "./types";
import type { MapCanvasLabels } from "./canvas-labels";

const ACTION =
  "pointer-events-auto rounded px-1 text-[var(--color-accent-primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] disabled:cursor-wait disabled:opacity-60";

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/** One clause of the sentence, after the separator. */
function Part({ children }: { children: ReactNode }) {
  return (
    <>
      {" · "}
      {children}
    </>
  );
}

function Action({
  pending,
  onClick,
  children,
}: {
  pending: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button type="button" className={ACTION} disabled={pending} onClick={onClick}>
      {children}
    </button>
  );
}

/** What the cap left out, in the units the server could count. */
function omittedText(scope: MapScope): string {
  const causes = scope.omitted.performanceFiles;
  if (causes == null) return `${scope.omitted.files.toLocaleString()} not drawn`;
  if (causes === 0) return "every file with a cause worth fixing now is drawn";
  return `${causes.toLocaleString()} ${plural(causes, "file", "files")} with causes worth fixing now not drawn`;
}

function HeldToggle({
  scope,
  pending,
  onChange,
}: {
  scope: MapScope;
  pending: boolean;
  onChange: (include: boolean) => void;
}) {
  const held = scope.held ?? 0;
  if (held <= 0 && !scope.includeHeld) return null;
  return (
    <Part>
      <Action pending={pending} onClick={() => onChange(!scope.includeHeld)}>
        {scope.includeHeld
          ? "Hide vendored and generated files"
          : `Include ${held.toLocaleString()} vendored and generated ${plural(held, "file", "files")}`}
      </Action>
    </Part>
  );
}

function SearchPart({
  scope,
  matches,
  query,
  capped,
  pending,
  onFindUndrawn,
}: {
  scope: MapScope;
  matches: number;
  query: string;
  capped: boolean;
  pending: boolean;
  onFindUndrawn?: ((query: string) => void) | undefined;
}) {
  const pinned = scope.query && scope.query.text === query ? scope.query : null;
  return (
    <Part>
      <span className="tabular-nums">{matches.toLocaleString()}</span>
      {pinned
        ? ` of ${pinned.matches.toLocaleString()} ${plural(pinned.matches, "match", "matches")} drawn`
        : ` ${plural(matches, "match", "matches")} here`}
      {onFindUndrawn && capped && !pinned ? (
        <Part>
          <Action pending={pending} onClick={() => onFindUndrawn(query)}>
            Find matches outside the field
          </Action>
        </Part>
      ) : null}
    </Part>
  );
}

export function MapScopeNote({
  scope,
  matches,
  query,
  drawMoreStep,
  pending,
  onDrawMore,
  onIncludeHeldChange,
  onFindUndrawn,
}: {
  scope: MapScope;
  matches: number;
  query: string;
  drawMoreStep: number;
  pending: boolean;
  onDrawMore?: (() => void) | undefined;
  onIncludeHeldChange?: ((include: boolean) => void) | undefined;
  onFindUndrawn?: ((query: string) => void) | undefined;
}) {
  const capped = scope.shown < scope.eligible;
  const more = Math.min(drawMoreStep, scope.eligible - scope.shown);
  return (
    <div
      data-testid="map-scope"
      className="pointer-events-none absolute bottom-3 right-3 z-20 max-w-[64%] rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-glass)] px-2.5 py-1.5 text-right text-xs text-[var(--color-text-tertiary)] shadow-sm backdrop-blur-sm sm:max-w-[52%]"
    >
      <span className="font-mono tabular-nums">
        {scope.shown.toLocaleString()} of {scope.eligible.toLocaleString()}
      </span>{" "}
      files drawn
      {capped ? <Part>{omittedText(scope)}</Part> : null}
      {capped && onDrawMore && more > 0 ? (
        <Part>
          <Action pending={pending} onClick={onDrawMore}>
            Draw {more.toLocaleString()} more
          </Action>
        </Part>
      ) : null}
      {onIncludeHeldChange ? (
        <HeldToggle scope={scope} pending={pending} onChange={onIncludeHeldChange} />
      ) : null}
      {query ? (
        <SearchPart
          scope={scope}
          matches={matches}
          query={query}
          capped={capped}
          pending={pending}
          onFindUndrawn={onFindUndrawn}
        />
      ) : null}
    </div>
  );
}

/**
 * The lazily loaded field's scope: how many files are drawn so far against
 * the repository, and how to see the rest. Every area is complete once loaded,
 * so the only thing to say is how far the reader has zoomed in.
 */
/**
 * The groups the map holds out by default (tests, build files, ...), each a
 * toggle that brings it back. The server names the groups and counts them;
 * the host keeps which are brought back in the URL.
 */
export interface HeldGroups {
  /** The held origins, in the server's order. */
  default: readonly string[];
  /** Files per held origin, repository-wide. */
  counts: Readonly<Record<string, number>>;
  /** Origins brought back. */
  include: readonly string[];
  onChange: (include: string[]) => void;
}

function HeldToggles({ groups, labels }: { groups: HeldGroups; labels: MapCanvasLabels }) {
  const present = groups.default.filter((o) => (groups.counts[o] ?? 0) > 0);
  if (present.length === 0) return null;
  return (
    <>
      {present.map((origin) => {
        const on = groups.include.includes(origin);
        const name = labels.originName(origin);
        const next = on ? groups.include.filter((o) => o !== origin) : [...groups.include, origin];
        return (
          <Part key={origin}>
            <button
              type="button"
              onClick={() => groups.onChange(next)}
              className={ACTION}
            >
              {on ? labels.hideHeld(name) : labels.showHeld(name, groups.counts[origin] ?? 0)}
            </button>
          </Part>
        );
      })}
    </>
  );
}

export function LoadedNote({
  loaded,
  total,
  matches,
  held,
  labels,
}: {
  loaded: number;
  total: number;
  /** Search matches among the loaded files, or null with no search. */
  matches: number | null;
  held?: HeldGroups | undefined;
  labels: MapCanvasLabels;
}) {
  return (
    <div
      data-testid="map-scope"
      className="pointer-events-none absolute bottom-3 right-3 z-20 max-w-[64%] rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-glass)] px-2.5 py-1.5 text-right text-xs text-[var(--color-text-tertiary)] shadow-sm backdrop-blur-sm sm:max-w-[52%]"
    >
      {loaded === 0 ? (
        <>
          <span className="font-mono tabular-nums">{total.toLocaleString()}</span> files in folders ·
          zoom into one to see its files
        </>
      ) : (
        <>
          <span className="font-mono tabular-nums">
            {loaded.toLocaleString()} of {total.toLocaleString()}
          </span>{" "}
          files loaded{loaded < total ? " · zoom in for more" : ""}
        </>
      )}
      {matches !== null ? (
        <Part>
          <span className="tabular-nums">{matches.toLocaleString()}</span>{" "}
          {plural(matches, "match", "matches")} loaded
        </Part>
      ) : null}
      {held ? <HeldToggles groups={held} labels={labels} /> : null}
    </div>
  );
}
