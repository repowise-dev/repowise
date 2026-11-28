/**
 * `devicePixelRatio` can change with no CSS resize (the window moves to a
 * monitor with another scale), which a ResizeObserver never reports, leaving a
 * blurry or oversized backing store. A `resolution` query pinned to the current
 * ratio fires once when it changes; it is re-pinned each time. Browser code;
 * inert where `matchMedia` is missing.
 */
export class PixelRatioWatch {
  /** Matches only the current pixel ratio, so its `change` means the ratio moved. */
  private query: MediaQueryList | null = null;

  constructor(private readonly onChange: () => void) {
    this.pin();
  }

  private pin(): void {
    this.query?.removeEventListener("change", this.fire);
    this.query = null;
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    this.query = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
    this.query.addEventListener("change", this.fire);
  }

  private readonly fire = (): void => {
    this.onChange();
    this.pin();
  };

  destroy(): void {
    this.query?.removeEventListener("change", this.fire);
    this.query = null;
  }
}
