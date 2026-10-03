/**
 * One animation at a time for the pane, whichever tab drives it: a frame every
 * FRAME_MS for the animation's length, each frame blitted into the Raster it
 * names, one blit at a time. A frame source returning null (its tab went off
 * screen) ends the animation; a refused blit stops it and asks for a redraw,
 * which draws the resting frame. It never sees `$`: register.ts hands it
 * closures from each hook it enters (`use`), and the timer uses the latest,
 * never one kept from an earlier hook.
 */

import { FRAME_MS } from "./views/overlay";

export interface AnimIO {
  redraw(): void;
  /** Debug log only (`--debug-file`), never the user's screen. */
  debug(message: string): void;
  blit(key: string, cells: string, columns: number, rows: number): Promise<{ deny?: string }>;
}

/** One frame for a Raster; null once its tab is off screen. */
export type Frame = { key: string; cells: string; columns: number; rows: number } | null;

/** Where an animation stands: its kind and `t` from 0 to 1. */
export interface Step {
  kind: string;
  t: number;
}

export class Animator {
  private anim: { kind: string; start: number; duration: number } | null = null;
  private timer: ReturnType<typeof setInterval> | undefined;
  private blitting = false;
  private stopped = false;
  private io: AnimIO | null = null;

  /** The closures of the hook running now; the timer's next frame goes through them. */
  use(io: AnimIO): void {
    this.io = io;
  }

  progress(now: number): Step | undefined {
    const a = this.anim;
    return a === null ? undefined : { kind: a.kind, t: Math.min(1, (now - a.start) / a.duration) };
  }

  /**
   * Plays `kind` for `duration` ms, replacing whatever played. `redraw` false
   * when called from a render, which already draws the first step.
   */
  play(io: AnimIO, kind: string, duration: number, screen: (step: Step) => Frame, redraw = true): void {
    this.stop();
    if (this.stopped) return;
    this.io = io;
    this.anim = { kind, start: Date.now(), duration };
    if (redraw) io.redraw();
    this.timer = setInterval(() => this.tick(screen), FRAME_MS);
  }

  stop(): void {
    clearInterval(this.timer);
    this.timer = undefined;
    this.anim = null;
  }

  /** For good: a disposed session plays nothing more. */
  end(): void {
    this.stopped = true;
    this.stop();
  }

  /** A timer's frame: never throws out of the timer; a failure stops the animation. */
  private tick(screen: (step: Step) => Frame): void {
    try {
      this.frame(screen);
    } catch (err) {
      this.failed(err);
    }
  }

  /** A failed frame: the animation stops and the failure goes to the debug log. */
  private failed(err: unknown): void {
    this.stop();
    this.io?.debug(`animation failed: ${String(err)}`);
  }

  private frame(screen: (step: Step) => Frame): void {
    const step = this.progress(Date.now());
    const frame = step === undefined ? null : screen(step);
    const io = this.io;
    if (step === undefined || frame === null || io === null) return this.stop();
    // One blit at a time: a slow surface drops frames rather than queueing them.
    if (this.blitting) return;
    if (step.t >= 1) this.stop();
    this.blitting = true;
    io.blit(frame.key, frame.cells, frame.columns, frame.rows)
      .then((r) => r.deny === undefined || this.refused(io, r.deny))
      .catch((err: unknown) => this.refused(io, String(err)))
      .finally(() => {
        this.blitting = false;
      });
  }

  /** Resized, closed or refused under the animation: stop and let a redraw show the resting frame. */
  private refused(io: AnimIO, why: string): void {
    io.debug(`blit refused: ${why}`);
    this.stop();
    if (!this.stopped) io.redraw();
  }
}
