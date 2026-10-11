/**
 * Dependency-structure matrix (DSM) — the dense governance view over the system
 * graph. `buildDsm` reshapes the graph (+ conformance overlays) into a matrix;
 * `DsmMatrixView` renders it. Both are pure over the shared `SystemGraph` /
 * `ConformanceReport` types, so the hosted frontend reuses them unchanged.
 */

export { buildDsm } from "./dsm";
export { DsmMatrixView, type DsmMatrixViewProps } from "./dsm-matrix";
