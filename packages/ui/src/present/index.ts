// Present mode — on-the-fly slide deck + guided walkthrough over already-loaded
// wiki pages. Framework-light and self-contained so the OSS dashboard and a
// future hosted app share one implementation: the host supplies DocPage[] and
// renders <PresentOverlay>.

export { buildPresentModel, canPresent } from "./build-present-model";
export { PresentOverlay, type PresentMode } from "./present-overlay";
export { PresentButton } from "./present-button";
export type { PresentModel, PresentSlide, PresentStep, PresentSlideKind } from "./types";
