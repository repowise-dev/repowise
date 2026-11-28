// Kept free of imports so the offline test suite can load it on its own.

/** Whether an open carries a target (a file, page, plan) rather than none. */
function hasParams(params: unknown): boolean {
  return (
    !!params &&
    typeof params === "object" &&
    Object.values(params).some((v) => v !== undefined && v !== null && v !== "")
  );
}

/**
 * Whether revealing an already-open panel with `next` params must re-send its
 * init. Only a plain open of a plainly opened panel skips it: that init would
 * be identical, and re-sending it remounts the view.
 */
export function reinitOnReveal(next: unknown, current: unknown): boolean {
  return hasParams(next) || hasParams(current);
}
