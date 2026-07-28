/**
 * The UI's answer to "is this ours?" must match the backend's.
 *
 * These sites each tested `startsWith("external:")` by hand, which misses the
 * `framework:` nodes the framework-edge pass synthesises. The backend excludes
 * those from layers, callers and tours; the UI was still drawing them as one
 * of the repository's own files, so the same node was described two ways
 * depending on which surface you looked at.
 */
import { describe, it, expect } from "vitest";
import { isExternalModuleId } from "../../src/graph/sigma/graphology-adapter";

describe("isExternalModuleId", () => {
  it("covers both spellings of code we do not own", () => {
    expect(isExternalModuleId("external:react")).toBe(true);
    expect(isExternalModuleId("framework:typo3-core")).toBe(true);
  });

  it("covers a crate whose name carries the symbol separator", () => {
    // Rust import resolution builds the node id from the raw module path.
    expect(isExternalModuleId("external:serde::Deserialize")).toBe(true);
  });

  it("leaves our own modules alone", () => {
    expect(isExternalModuleId("packages/core")).toBe(false);
    expect(isExternalModuleId("src/main.py")).toBe(false);
    expect(isExternalModuleId("src/main.py::run")).toBe(false);
  });
});
