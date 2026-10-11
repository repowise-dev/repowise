/**
 * Shared graph vocabulary.
 *
 * This file used to also export a `GraphContext` / `GraphProvider` /
 * `useGraphContext` trio mirroring ~18 fields of GraphFlow's state. It had zero
 * consumers — every one of those fields reaches the canvas as a prop, and Sigma
 * owns hover and highlight itself — so the provider only re-rendered the shell
 * and rebuilt an 18-key object on every interaction. Removed rather than kept
 * "just in case": props already carry this, and a second path would drift.
 */

export type Signal = "dead" | "hot" | "architecture" | "hideTests";
