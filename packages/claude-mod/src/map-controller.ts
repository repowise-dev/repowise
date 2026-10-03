/**
 * The map's session: what Claude looked at, the feed, the importers of the
 * last edit, and the animations. One instance per session; a disposed one
 * drops every late answer, so nothing from an earlier session lands in this
 * one. It never sees `$`: register.ts hands it closures (MapIO).
 *
 * Quiet at rest: no request leaves until the person asks for the map (/lens,
 * or an auto-open that was placed).
 */

import { analyzeBlastRadius } from "@repowise-dev/api-client/blast-radius";
import { getHealthMap } from "@repowise-dev/api-client/code-health";
import type { HealthMapFeed } from "@repowise-dev/types/health";
import { formatRelativeTimeOrNull } from "@repowise-dev/ui/lib/format";
import { withTimeout } from "./data/transport";
import type { UiOpenResult } from "./mod-api";
import { relativeTo } from "./model/events";
import { initialTrail, reduceTrail, type TrailAction, type TrailState } from "./model/trail";
import { MAP_COPY, notPlacedLine } from "./views/copy";
import type { Node } from "./views/elements";
import { layoutMap, type MapLayout } from "./views/map";
import { mapPaneView, mapSize, noticeView, type PaneSize } from "./views/mapPane";
import { desktopSize, svgPaneView } from "./views/mapSvg";
import { FLASH_MS, FRAME_MS, RIPPLE_MS, frameCells, resolveOverlay, rippleRadius, type Anim } from "./views/overlay";

/** The server's own maximum: Django's 2,347 drawable files fit in one call. */
const MAP_CAP = 4_000;
const MAP_TIMEOUT_MS = 15_000;
const CALLERS_TIMEOUT_MS = 20_000;
/** Direct importers only: 0.6 s on Django's query.py, against 2 s for three levels. */
const CALLERS_DEPTH = 1;

export interface MapIO {
  redraw(): void;
  /** Debug log only (`--debug-file`), never the user's screen. */
  debug(message: string): void;
  blit(cells: string, columns: number, rows: number): Promise<{ deny?: string }>;
  /** `focus` only when the person asked for the pane; an automatic open never takes the keyboard. */
  openPane(focus: boolean): Promise<UiOpenResult>;
  closePane(): Promise<void>;
}

/** The repo the map draws, once discovery found the local server for it. */
export interface MapRepo {
  id: string;
  root: string;
  updatedAt: string | null;
  caseInsensitive: boolean;
}

type Feed = { status: "idle" | "loading" | "failed" } | { status: "ready"; data: HealthMapFeed };

export interface PaneInput extends PaneSize {
  /** The surface drawing the pane: the terminal (cells) or the desktop app (SVG). */
  surface: string | undefined;
  /** Why there is no repo to draw, when there is none. */
  notice: string;
}

/** What an animation draws on: the frame for a step, or null once nothing is on screen. */
type Screen = (step: Anim) => { cells: string; columns: number; rows: number } | null;

/**
 * Plays a flash or a ripple with blits, a frame every FRAME_MS for about a
 * second, one blit at a time. Stops on a refused blit and asks for a redraw,
 * which draws the resting frame.
 */
class Animator {
  private anim: { kind: Anim["kind"]; start: number; duration: number } | null = null;
  private timer: ReturnType<typeof setInterval> | undefined;
  private blitting = false;
  private stopped = false;

  progress(now: number): Anim | undefined {
    const a = this.anim;
    return a === null ? undefined : { kind: a.kind, t: Math.min(1, (now - a.start) / a.duration) };
  }

  play(io: MapIO, kind: Anim["kind"], screen: Screen): void {
    this.stop();
    if (this.stopped) return;
    this.anim = { kind, start: Date.now(), duration: kind === "flash" ? FLASH_MS : RIPPLE_MS };
    io.redraw();
    this.timer = setInterval(() => this.tick(io, screen), FRAME_MS);
  }

  stop(): void {
    clearInterval(this.timer);
    this.timer = undefined;
    this.anim = null;
  }

  /** For good: a disposed map plays nothing more. */
  end(): void {
    this.stopped = true;
    this.stop();
  }

  private tick(io: MapIO, screen: Screen): void {
    const step = this.progress(Date.now());
    const frame = step === undefined ? null : screen(step);
    if (step === undefined || frame === null) return this.stop();
    // One blit at a time: a slow surface drops frames rather than queueing them.
    if (this.blitting) return;
    if (step.t >= 1) this.stop();
    this.blitting = true;
    io.blit(frame.cells, frame.columns, frame.rows)
      .then((r) => r.deny === undefined || this.refused(io, r.deny))
      .catch((err: unknown) => this.refused(io, String(err)))
      .finally(() => {
        this.blitting = false;
      });
  }

  /** Resized, closed or refused under the animation: stop and let a redraw show the resting frame. */
  private refused(io: MapIO, why: string): void {
    io.debug(`blit refused: ${why}`);
    this.stop();
    if (!this.stopped) io.redraw();
  }
}

export class LensMap {
  trail: TrailState = initialTrail;
  private disposed = false;
  /** One band row the map asks for (the pane could not open), until the turn ends. */
  private band: string | null = null;
  private repo: MapRepo | null = null;
  private feed: Feed = { status: "idle" };
  private requested = false;
  private autoOpenTried = false;
  /** The latest edit's absolute path with its case, for the importers request. */
  private editPath: string | null = null;
  /** The edit whose importers were asked for, so each is asked once. */
  private callersAsked = 0;
  private drawn: { layout: MapLayout; root: string } | null = null;
  private layoutCache: { data: HealthMapFeed; columns: number; rows: number; layout: MapLayout } | null = null;
  private readonly animator = new Animator();
  private reducedMotion = false;

  private readonly autoOpenOnRead: boolean;

  constructor(autoOpenOnRead: boolean) {
    this.autoOpenOnRead = autoOpenOnRead;
  }

  /** The band row the map wants shown, if any. */
  bandLine(): string | null {
    return this.band;
  }

  /** The person's `prefersReducedMotion` setting: resting frames only. */
  setReducedMotion(on: boolean): void {
    this.reducedMotion = on;
  }

  dispose(): void {
    this.disposed = true;
    this.animator.end();
  }

  setRepo(io: MapIO, repo: MapRepo | null): void {
    this.repo = repo;
    this.fetchWanted(io);
  }

  /** One observed tool call; `editPath` is the edited file's absolute path, case kept. */
  observe(io: MapIO, action: TrailAction, editPath: string | null): void {
    if (action.type === "edit") this.editPath = editPath;
    this.apply(io, action);
    if (action.type === "edit") this.fetchWanted(io);
    if (action.type === "read") void this.autoOpen(io);
  }

  /** /lens: open the pane with focus (asked, so placed at any width) and start what it shows. */
  async request(io: MapIO): Promise<void> {
    this.requested = true;
    this.band = null;
    if (this.feed.status === "failed") this.feed = { status: "idle" };
    const opened = await io.openPane(true);
    if (!opened.isPlaced) this.band = notPlacedLine(opened.reason);
    this.fetchWanted(io);
    io.redraw();
  }

  /** A main-loop turn ended: the band row has been seen. */
  turnEnded(): void {
    this.band = null;
  }

  /** Another tab is shown: nothing of the map is on screen to animate. */
  offScreen(): void {
    this.drawn = null;
  }

  /** The pane's tree; starts the feed fetch when the map is wanted and not yet asked for. */
  paneTree(io: MapIO, pane: PaneInput): Node {
    const columns = pane.bodyColumns;
    this.drawn = null;
    // The terminal draws cells and the desktop app SVG; other surfaces get a line.
    const desktop = pane.surface === "desktop";
    if (pane.surface !== undefined && pane.surface !== "terminal" && !desktop) return noticeView(MAP_COPY.desktop, columns);
    const repo = this.repo;
    if (repo === null) return noticeView(pane.notice, columns);
    this.requested = true;
    this.fetchWanted(io);
    const feed = this.feed;
    if (feed.status !== "ready") return noticeView(feed.status === "failed" ? MAP_COPY.failed : MAP_COPY.loading, columns);
    const size = desktop ? desktopSize(mapSize(pane)) : mapSize(pane);
    const layout = this.layoutFor(feed.data, size, repo.caseInsensitive);
    const resolved = resolveOverlay(layout, this.trail, repo.root);
    const scope = {
      drawn: layout.drawn.length,
      repositoryTotal: feed.data.repository_total,
      indexed: formatRelativeTimeOrNull(repo.updatedAt, "") || null,
      beyondCap: feed.data.omitted.files,
      cap: feed.data.cap,
    };
    // No blits on the desktop: its map is the resting frame, redrawn as the trail grows.
    if (desktop) return svgPaneView(layout, resolved, scope);
    this.drawn = { layout, root: repo.root };
    const cells = frameCells(layout, resolved.overlay, this.animator.progress(Date.now()));
    return mapPaneView(layout, cells, resolved, scope);
  }

  private layoutFor(data: HealthMapFeed, size: { columns: number; rows: number }, caseInsensitive: boolean): MapLayout {
    const cached = this.cachedLayout(data, size);
    if (cached !== null) return cached;
    const layout = layoutMap(data.files, size.columns, size.rows, caseInsensitive);
    this.layoutCache = { data, columns: size.columns, rows: size.rows, layout };
    return layout;
  }

  private cachedLayout(data: HealthMapFeed, size: { columns: number; rows: number }): MapLayout | null {
    const c = this.layoutCache;
    const same = c !== null && c.data === data && c.columns === size.columns && c.rows === size.rows;
    return same ? c.layout : null;
  }

  private apply(io: MapIO, action: TrailAction): void {
    const next = reduceTrail(this.trail, action);
    if (next === this.trail) return;
    this.trail = next;
    if (action.type === "search") this.animate(io, "flash");
    else if (action.type === "callers" && action.callers.status === "ready") this.animate(io, "ripple");
    else io.redraw();
  }

  /** The feed and the latest edit's importers, once the map is wanted and the repo known. */
  private fetchWanted(io: MapIO): void {
    if (!this.requested || this.repo === null) return;
    this.loadFeed(io, this.repo);
    this.loadCallers(io, this.repo);
  }

  private loadFeed(io: MapIO, repo: MapRepo): void {
    if (this.feed.status !== "idle") return;
    this.feed = { status: "loading" };
    withTimeout(getHealthMap(repo.id, { cap: MAP_CAP }), MAP_TIMEOUT_MS, "health map")
      .then((data) => {
        this.feed = { status: "ready", data };
      })
      .catch((err: unknown) => {
        this.feed = { status: "failed" };
        io.debug(`health map failed: ${String(err)}`);
      })
      .finally(() => {
        if (!this.disposed) io.redraw();
      });
  }

  private loadCallers(io: MapIO, repo: MapRepo): void {
    const edits = this.trail.edits;
    if (this.editPath === null || this.callersAsked === edits) return;
    this.callersAsked = edits;
    const rel = relativeTo(this.editPath, repo.root, repo.caseInsensitive);
    if (rel === null) return this.apply(io, { type: "callers", edits, callers: { status: "failed" } });
    const request = { changed_files: [rel], max_depth: CALLERS_DEPTH };
    withTimeout(analyzeBlastRadius(repo.id, request), CALLERS_TIMEOUT_MS, "importers")
      .then((r) => {
        const paths = [...new Set(r.transitive_affected.map((t) => t.path))].filter((p) => p !== rel);
        if (!this.disposed) this.apply(io, { type: "callers", edits, callers: { status: "ready", paths } });
      })
      .catch((err: unknown) => {
        io.debug(`importers failed: ${String(err)}`);
        if (!this.disposed) this.apply(io, { type: "callers", edits, callers: { status: "failed" } });
      });
  }

  /** The first read opens the map when the toggle is on and the pane would be placed; never forced. */
  private async autoOpen(io: MapIO): Promise<void> {
    if (!this.mayAutoOpen()) return;
    this.autoOpenTried = true;
    const opened = await io.openPane(false);
    if (this.disposed || this.requested) return;
    if (opened.isPlaced) {
      this.requested = true;
      this.fetchWanted(io);
      return;
    }
    // Below the width an unasked pane needs: close it rather than let it pop up later.
    await io.closePane();
    this.band = MAP_COPY.waiting;
    io.redraw();
  }

  /** Once, with the toggle on, before the person asked, and only for a repo with a map. */
  private mayAutoOpen(): boolean {
    return this.autoOpenOnRead && !this.autoOpenTried && !this.requested && this.repo !== null;
  }

  /** Whether an animation would show anything: a map on screen, motion allowed, a ripple with somewhere to go. */
  private canAnimate(kind: Anim["kind"]): boolean {
    const on = this.drawn;
    if (this.reducedMotion || on === null) return false;
    return kind === "flash" || rippleRadius(on.layout, resolveOverlay(on.layout, this.trail, on.root).overlay) > 0;
  }

  /** A flash or a ripple when it would show; otherwise (reduced motion, nothing on screen) the resting frame. */
  private animate(io: MapIO, kind: Anim["kind"]): void {
    this.animator.stop();
    if (!this.canAnimate(kind)) return io.redraw();
    this.animator.play(io, kind, (step) => this.frame(step));
  }

  /** The current trail on the drawn map at one step, or null once the map is off screen. */
  private frame(step: Anim): { cells: string; columns: number; rows: number } | null {
    const on = this.drawn;
    if (on === null) return null;
    const cells = frameCells(on.layout, resolveOverlay(on.layout, this.trail, on.root).overlay, step);
    return { cells, columns: on.layout.columns, rows: on.layout.rows };
  }
}
