/**
 * The pointer side of the renderer's hover pick. Browser-free.
 *
 * Hover is resolved once per frame, not per pointer event: `move` only records
 * the newest client point, and the next frame takes it, however many
 * pointermove events arrived in between. A frame drawn with a new camera,
 * viewport or scene re-arms the pick at the resting point, so the hover
 * follows a wheel, key or fly without waiting for a pointer move.
 */

export interface ClientPoint {
  x: number;
  y: number;
}

export class PointerPick {
  /** The newest pointer move, awaiting its once-per-frame pick. */
  private pending: ClientPoint | null = null;
  /** Where the pointer rests over the canvas, or null once it leaves or drags. */
  private resting: ClientPoint | null = null;
  /** What the last frame was drawn with, to tell when the cards moved. */
  private drawnCam: unknown = null;
  private drawnVp: unknown = null;
  private drawnScene: unknown = null;

  move(x: number, y: number): void {
    this.pending = { x, y };
    this.resting = this.pending;
  }

  /** The pointer left, or a press or drag owns it: no pick until it moves again. */
  clear(): void {
    this.pending = null;
    this.resting = null;
  }

  /** The point to pick this frame, if any; the pick is then no longer pending. */
  take(): ClientPoint | null {
    const p = this.pending;
    this.pending = null;
    return p;
  }

  /**
   * After a draw: true when the cards moved under a resting pointer, which
   * re-arms the pick (the caller schedules a frame to take it).
   */
  afterDraw(cam: unknown, vp: unknown, scene: unknown): boolean {
    const moved = cam !== this.drawnCam || vp !== this.drawnVp || scene !== this.drawnScene;
    this.drawnCam = cam;
    this.drawnVp = vp;
    this.drawnScene = scene;
    if (!moved || !this.resting || this.pending) return false;
    this.pending = this.resting;
    return true;
  }
}
