/**
 * The Repowise owl on one line, after the CLI mascot's mini form `{◉ ◉}`
 * (packages/cli/src/repowise/cli/ui/mascot.py): `{◉,◉}`. It says what is
 * happening through its eyes alone, calmly. The strokes take the terminal's
 * own foreground; open eyes are amber. Every glyph is one cell wide (none is
 * East Asian wide; `─`, `◐` and `•` are ambiguous width, one cell outside
 * CJK locales, as the CLI's own owl already assumes).
 */

import { text, type Node } from "./elements";

export const EYES = { open: "◉", shut: "─", side: "◐", happy: "^", still: "•" } as const;

/** A slow blink: open for nine frames, shut for one. */
const BLINK: readonly string[] = [...Array<string>(9).fill(EYES.open), EYES.shut];
export const BLINK_MS = 250;

/**
 * Asleep at rest, watching (a slow blink) while Claude works, looking aside
 * while a Repowise call is in flight, happy for a moment after an answer,
 * neutral after a turn that stopped or did not finish.
 */
export type OwlState = "asleep" | "watching" | "asking" | "happy" | "stopped";

const EYES_OF: Record<Exclude<OwlState, "watching">, string> = {
  asleep: EYES.shut,
  asking: EYES.side,
  happy: EYES.happy,
  stopped: EYES.still,
};

/** The eyes for a state at `now`; `still` (reduced motion) holds them open. */
export function owlEyes(state: OwlState, now: number, still: boolean): string {
  if (state !== "watching") return EYES_OF[state];
  return still ? EYES.open : (BLINK[Math.floor(now / BLINK_MS) % BLINK.length] as string);
}

export const OWL_WIDTH = 5;

/**
 * `{◉,◉}`: strokes in the terminal's foreground, eyes in `amber` while open.
 * Asleep, the whole owl is dim.
 */
export function owl(state: OwlState, eyes: string, amber: string): Node[] {
  if (state === "asleep") return [text(`{${eyes},${eyes}}`, { dimColor: true })];
  const eye = (): Node => (eyes === EYES.shut || eyes === EYES.still ? text(eyes) : text(eyes, { color: amber }));
  return [text("{"), eye(), text(","), eye(), text("}")];
}
