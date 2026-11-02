// Present mode: a short narrated deck over already-generated wiki pages.
// Framework-light and self-contained so every host shares one implementation:
// the host gathers pages with `loadPresentSource`, builds the model and renders
// <PresentOverlay>.

export { buildPresentModel, canPresent } from "./build-present-model";
export { loadPresentSource } from "./load-present-source";
export { PresentOverlay } from "./present-overlay";
export { PresentButton } from "./present-button";
export type { PresentModel, PresentSource } from "./types";
