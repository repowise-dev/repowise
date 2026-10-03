/**
 * Flow's session: its model, the redraws (at most 8 a second, and only
 * while the Flow tab is on screen) and the owl's clock (ticking only while
 * Claude works and Flow is on screen). One instance per session; a disposed
 * one stops its timers. It never sees `$`: each hook hands it a fresh IO,
 * and timers use the latest one, never one kept from an earlier hook.
 */

import { initialFlow, isWorking, reduceFlow, type FlowAction, type FlowState } from "./model/flow";
import type { ChangeRisk } from "./model/review";
import type { FileContext, Mode } from "./model/session";
import type { Node } from "./views/elements";
import { flowView, HAPPY_MS } from "./views/flow";
import { BLINK_MS } from "./views/owl";

/** At most 8 redraws a second. */
export const REDRAW_MS = 125;
/** With reduced motion the owl holds still; the turn's clock still counts seconds. */
const STILL_TICK_MS = 1_000;
/** Redraws unanswered this long mean the pane is gone (closed, or another tab). */
const GONE_MS = 1_000;

export interface FlowIO {
  redraw(): void;
  debug(message: string): void;
}

export interface FlowPane {
  columns: number;
  rows: number;
  mode: Mode | null;
  contexts: Readonly<Record<string, FileContext>>;
  review: ChangeRisk | null;
  light?: boolean;
}

export class LensFlow {
  state: FlowState = initialFlow;
  /** Flow is on screen: set by its render; cleared by another tab, or by a redraw no render answered. */
  private shown = false;
  private still = false;
  private io: FlowIO | null = null;
  private lastDraw = Number.NEGATIVE_INFINITY;
  /** When the first redraw no Flow render has answered was asked for; null once one did. */
  private unansweredSince: number | null = null;
  private pending: ReturnType<typeof setTimeout> | undefined;
  private ticker: ReturnType<typeof setInterval> | undefined;
  private happy: ReturnType<typeof setTimeout> | undefined;

  setReducedMotion(on: boolean): void {
    this.still = on;
  }

  /** Another tab is shown, or the pane is gone: nothing of Flow is on screen. */
  hide(): void {
    this.shown = false;
    this.stopTick();
  }

  dispatch(io: FlowIO, action: FlowAction): void {
    this.io = io;
    const next = reduceFlow(this.state, action);
    if (next === this.state) return;
    this.state = next;
    if (action.type === "turnEnded") this.afterTurn();
    this.request();
  }

  /** The tab's tree at `now`; a render marks Flow on screen and keeps the owl's clock going while Claude works. */
  paneTree(io: FlowIO, pane: FlowPane, now: number): Node {
    this.io = io;
    this.shown = true;
    this.unansweredSince = null;
    this.ensureTick();
    return flowView(this.state, { ...pane, now, still: this.still });
  }

  dispose(): void {
    this.hide();
    clearTimeout(this.pending);
    clearTimeout(this.happy);
    this.pending = undefined;
    this.happy = undefined;
    this.io = null;
  }

  /** A redraw now, or once the throttle allows; nothing while Flow is not on screen. */
  private request(): void {
    if (!this.shown || this.pending !== undefined) return;
    const wait = this.lastDraw + REDRAW_MS - Date.now();
    if (wait <= 0) return this.draw();
    this.pending = setTimeout(() => this.timer(() => ((this.pending = undefined), true)), wait);
  }

  /** A timer's body: its step says whether to redraw; a failure is logged, never thrown into the runtime. */
  private timer(step: () => boolean): void {
    try {
      if (step()) this.draw();
    } catch (err) {
      this.io?.debug(`flow timer failed: ${String(err)}`);
    }
  }

  /** Redraws, unless redraws have gone unanswered for a while: then the pane is gone and Flow stops asking. */
  private draw(): void {
    const now = Date.now();
    if (this.unansweredSince !== null && now - this.unansweredSince >= GONE_MS) return this.hide();
    if (!this.shown || this.io === null) return;
    this.lastDraw = now;
    this.unansweredSince ??= now;
    this.io.redraw();
  }

  /** The owl is happy for a moment, then sleeps: one redraw when that moment ends. */
  private afterTurn(): void {
    this.stopTick();
    clearTimeout(this.happy);
    this.happy = setTimeout(() => this.timer(() => ((this.happy = undefined), true)), HAPPY_MS);
  }

  private ensureTick(): void {
    if (this.ticker !== undefined || !this.shown || !isWorking(this.state)) return;
    this.ticker = setInterval(() => this.timer(() => this.tick()), this.still ? STILL_TICK_MS : BLINK_MS);
  }

  /** Redraw while Claude works; stop once it is done. Off screen, `draw` stops it. */
  private tick(): boolean {
    if (isWorking(this.state)) return true;
    this.stopTick();
    return false;
  }

  private stopTick(): void {
    clearInterval(this.ticker);
    this.ticker = undefined;
  }
}
