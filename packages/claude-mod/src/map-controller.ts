/**
 * The map's session: what Claude looked at, the feed, the importers of the
 * last edit, and the animations. One instance per session; a disposed one
 * drops every late answer, so nothing from an earlier session lands in this
 * one. It never sees `$`: register.ts hands it closures (MapIO).
 *
 * Quiet at rest: no request leaves until the person asks for the map (/lens,
 * or an auto-open that was placed).
 */

import { analyzeBlastRadius, type BlastRadiusResponse } from "@repowise-dev/api-client/blast-radius";
import { getHealthMap } from "@repowise-dev/api-client/code-health";
import type { HealthMapFeed } from "@repowise-dev/types/health";
import { formatRelativeTimeOrNull } from "@repowise-dev/ui/lib/format";
import { Animator, type AnimIO, type Frame, type Step } from "./animator";
import { withTimeout } from "./data/transport";
import type { UiOpenResult } from "./mod-api";
import { relativeTo } from "./model/events";
import { NO_STORY, withImporters, type Reach, type Story } from "./model/story";
import { initialTrail, reduceTrail, type Callers, type TrailAction, type TrailState } from "./model/trail";
import { MAP_COPY, notPlacedLine, type ScopeFacts } from "./views/copy";
import type { Node } from "./views/elements";
import { layoutMap, type MapLayout, type MapStyle } from "./views/map";
import { mapPaneView, mapSize, noticeView, type PaneSize } from "./views/mapPane";
import { desktopSize, svgPaneView } from "./views/mapSvg";
import { FLASH_MS, NO_LIT, RIPPLE_MS, frameCells, notOnMap, resolveLit, rippleRadius, type Anim, type Lit } from "./views/overlay";
import { MAP_KEY } from "./views/mapPane";
import type { ThemeName } from "./views/theme";

/** The server's own maximum: Django's 2,347 drawable files fit in one call. */
const MAP_CAP = 4_000;
const MAP_TIMEOUT_MS = 15_000;
const CALLERS_TIMEOUT_MS = 20_000;
/** Direct importers only: 0.6 s on Django's query.py, against 2 s for three levels. */
const CALLERS_DEPTH = 1;

export interface MapIO extends AnimIO {
  /** `focus` only when the person asked for the pane; an automatic open never takes the keyboard. */
  openPane(focus: boolean): Promise<UiOpenResult>;
  closePane(): Promise<void>;
  /** The whole blast radius answer for an edited file, for Flow; the map keeps only the importers. */
  blastLanded?(path: string, r: BlastRadiusResponse): void;
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
  theme: ThemeName;
}

/** The current turn as it stands: what it lit (before its importers are joined) and its story. */
export type TurnSource = () => { lit: Lit; story: Story };

const NO_TURN: TurnSource = () => ({ lit: NO_LIT, story: NO_STORY });

function scopeFacts(layout: MapLayout, data: HealthMapFeed, repo: MapRepo, offMap: number): ScopeFacts {
  return {
    drawn: layout.drawn.length,
    shown: data.files.length,
    repositoryTotal: data.repository_total,
    indexed: formatRelativeTimeOrNull(repo.updatedAt, "") || null,
    beyondCap: data.omitted.files,
    dense: layout.dense,
    notOnMap: offMap,
  };
}

type LayoutKey = { data: HealthMapFeed; columns: number; rows: number; style: MapStyle };
const keyParts = (k: LayoutKey): unknown[] => [k.data, k.columns, k.rows, k.style.theme, k.style.health];
const sameKey = (a: LayoutKey, b: LayoutKey): boolean => keyParts(a).every((v, i) => v === keyParts(b)[i]);

export class LensMap {
  trail: TrailState = initialTrail;
  private disposed = false;
  /** One band row the map asks for (the pane could not open), until the turn ends. */
  private band: string | null = null;
  private repo: MapRepo | null = null;
  private feed: Feed = { status: "idle" };
  private requested = false;
  private autoOpenTried = false;
  /** The latest edit's absolute path with its case. */
  private editPath: string | null = null;
  /**
   * Every file edited this session (absolute, case kept), and the importers
   * of each, asked once per distinct path. Ceiling: never asked again, so an
   * index update mid-session is not seen; nothing tells Lens it changed.
   */
  private readonly edited = new Set<string>();
  private readonly importers = new Map<string, Callers>();
  private drawn: { layout: MapLayout; root: string } | null = null;
  private layoutCache: { key: LayoutKey; layout: MapLayout } | null = null;
  private reducedMotion = false;
  /** Health colours on the tiles: off by default (`lens_map_health`), toggled from the pane. */
  private health: boolean;
  /** Read at every frame, so an animation sees the turn as it is now. */
  private readonly turn: TurnSource;

  private readonly autoOpenOnRead: boolean;
  /** The pane's one animator, shared with the trail: whichever tab is shown drives it. */
  private readonly animator: Animator;

  constructor(autoOpenOnRead: boolean, animator: Animator = new Animator(), health = false, turn: TurnSource = NO_TURN) {
    this.autoOpenOnRead = autoOpenOnRead;
    this.animator = animator;
    this.health = health;
    this.turn = turn;
  }

  /** The importers of a repo-relative path, as far as they are known; null when never asked. */
  importersOf(path: string): Callers | null {
    return this.importers.get(this.keyOf(path)) ?? null;
  }

  private keyOf(rel: string): string {
    return this.repo?.caseInsensitive === false ? rel : rel.toLowerCase();
  }

  /** The pane's toggle: health colours on the tiles, or the quiet ghost map. */
  toggleHealth(io: MapIO): void {
    this.health = !this.health;
    io.redraw();
  }

  /** What the turn lit, with the latest edit's importers joined as they stand now. */
  private lighting(): { lit: Lit; reach: Reach | null } {
    return withImporters(this.turn().lit, (p) => this.importersOf(p));
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

  /**
   * A `/clear`: the conversation's trail and edits go, so nothing of it stays
   * lit. The feed, the repo and the importers already asked for (index data,
   * by path) stay, and so does an open pane.
   */
  clearConversation(io: MapIO): void {
    this.trail = initialTrail;
    this.edited.clear();
    this.editPath = null;
    this.band = null;
    io.redraw();
  }

  setRepo(io: MapIO, repo: MapRepo | null): void {
    this.repo = repo;
    this.fetchWanted(io);
  }

  /** One observed tool call; `editPath` is the edited file's absolute path, case kept. */
  observe(io: MapIO, action: TrailAction, editPath: string | null): void {
    if (action.type === "edit" && editPath !== null) this.edited.add(editPath);
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
    this.drawn = null;
    const notice = this.noticeFor(io, pane);
    if (notice !== null) return noticeView(notice, pane.bodyColumns);
    const { data } = this.feed as { data: HealthMapFeed };
    const repo = this.repo as MapRepo;
    // The terminal draws cells and the desktop app SVG, with no blits: its resting frame redraws as the turn goes on.
    const desktop = pane.surface === "desktop";
    const style: MapStyle = { theme: pane.theme, health: this.health };
    const size = mapSize(pane, this.health);
    const layout = this.layoutFor({ data, ...(desktop ? desktopSize(size) : size), style }, repo.caseInsensitive);
    const { lit, reach } = this.lighting();
    const overlay = resolveLit(layout, lit);
    const parts = { story: this.turn().story, reach, scope: scopeFacts(layout, data, repo, notOnMap(layout, lit)) };
    if (desktop) return svgPaneView(layout, overlay, parts);
    this.drawn = { layout, root: repo.root };
    return mapPaneView(layout, frameCells(layout, overlay, mapStep(this.animator.progress(Date.now()))), parts);
  }

  /** Why there is no map to draw yet (and the feed fetch started when wanted), or null once it can be drawn. */
  private noticeFor(io: MapIO, pane: PaneInput): string | null {
    if (pane.surface !== undefined && pane.surface !== "terminal" && pane.surface !== "desktop") return MAP_COPY.desktop;
    if (this.repo === null) return pane.notice;
    this.requested = true;
    this.fetchWanted(io);
    const status = this.feed.status;
    if (status === "ready") return null;
    return status === "failed" ? MAP_COPY.failed : MAP_COPY.loading;
  }

  /** The layout for this feed, size and style; laid out again only when one of them changed. */
  private layoutFor(key: LayoutKey, caseInsensitive: boolean): MapLayout {
    const c = this.layoutCache;
    if (c !== null && sameKey(c.key, key)) return c.layout;
    const layout = layoutMap(key.data.files, { columns: key.columns, rows: key.rows, caseInsensitive, style: key.style });
    this.layoutCache = { key, layout };
    return layout;
  }

  private apply(io: MapIO, action: TrailAction): void {
    const next = reduceTrail(this.trail, action);
    if (next === this.trail) return;
    this.trail = next;
    if (action.type === "search") this.animate(io, "flash");
    else if (action.type === "callers" && action.callers.status === "ready") this.animate(io, "ripple");
    else io.redraw();
  }

  /** The feed and the importers of every edited file not yet asked for, once the map is wanted and the repo known. */
  private fetchWanted(io: MapIO): void {
    if (!this.requested || this.repo === null) return;
    this.loadFeed(io, this.repo);
    for (const path of this.edited) this.loadCallers(io, this.repo, path);
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

  /** One edited file's importers, asked once; the answer is kept by path. */
  private loadCallers(io: MapIO, repo: MapRepo, abs: string): void {
    const rel = relativeTo(abs, repo.root, repo.caseInsensitive);
    const key = rel === null ? null : this.keyOf(rel);
    if (rel === null || key === null || this.importers.has(key)) return;
    this.importers.set(key, { status: "loading" });
    const request = { changed_files: [rel], max_depth: CALLERS_DEPTH };
    withTimeout(analyzeBlastRadius(repo.id, request), CALLERS_TIMEOUT_MS, "importers")
      .then((r) => {
        const paths = [...new Set(r.transitive_affected.map((t) => t.path))].filter((p) => p !== rel);
        if (this.disposed) return;
        this.landed(io, abs, { status: "ready", paths });
        io.blastLanded?.(rel, r);
      })
      .catch((err: unknown) => {
        io.debug(`importers failed: ${String(err)}`);
        if (!this.disposed) this.landed(io, abs, { status: "failed" });
      });
  }

  /** An answer for one edited file: kept by path; for the latest edit, also the trail's (which ripples). */
  private landed(io: MapIO, abs: string, callers: Callers): void {
    const rel = this.repo === null ? null : relativeTo(abs, this.repo.root, this.repo.caseInsensitive);
    if (rel !== null) this.importers.set(this.keyOf(rel), callers);
    if (abs === this.editPath) this.apply(io, { type: "callers", edits: this.trail.edits, callers });
    else io.redraw();
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
    return kind === "flash" || rippleRadius(on.layout, resolveLit(on.layout, this.lighting().lit)) > 0;
  }

  /**
   * A flash or a ripple when it would show; otherwise (reduced motion, nothing
   * on screen) the resting frame, leaving another tab's animation playing.
   */
  private animate(io: MapIO, kind: Anim["kind"]): void {
    if (!this.canAnimate(kind)) return io.redraw();
    this.animator.play(io, kind, kind === "flash" ? FLASH_MS : RIPPLE_MS, (step) => this.frame(step));
  }

  /** The current trail on the drawn map at one step, or null once the map is off screen. */
  private frame(step: Step): Frame {
    const on = this.drawn;
    const anim = mapStep(step);
    if (on === null || anim === undefined) return null;
    const cells = frameCells(on.layout, resolveLit(on.layout, this.lighting().lit), anim);
    return { key: MAP_KEY, cells, columns: on.layout.columns, rows: on.layout.rows };
  }
}

/** The animator's step when it is one of the map's own. */
function mapStep(step: Step | undefined): Anim | undefined {
  return step !== undefined && (step.kind === "flash" || step.kind === "ripple") ? (step as Anim) : undefined;
}
