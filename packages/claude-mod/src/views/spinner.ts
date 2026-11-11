/**
 * The spinner suffix: while a file tool runs on a file whose context has
 * already landed, name the file and its reach. Never waits for a fetch.
 */

import type { SessionState } from "../model/session";
import { spinnerLine } from "./copy";

/** null leaves the engine's suffix as it is. */
export function spinnerSuffix(state: SessionState): string | null {
  if (state.running === null) return null;
  const ctx = state.contexts[state.running.file];
  return ctx === undefined ? null : spinnerLine(state.running.file, ctx);
}
