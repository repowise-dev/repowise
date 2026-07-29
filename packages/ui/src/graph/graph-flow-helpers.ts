/**
 * Pure helpers extracted from GraphFlow to remove duplication.
 */

/**
 * Build the set of directed edge keys for a node trace/path, in both
 * directions. Used by both the execution-flow highlighter and the path-finder
 * result handler — they highlight the same way, so the key construction lives
 * here once. Mirrors Sigma's `source→target` edge-key convention.
 */
/**
 * Collapse an execution-flow trace onto the files it runs through.
 *
 * `calls` edges only ever connect symbol nodes, so a trace is a list of
 * `path/to/file.py::symbol` ids — but this canvas draws files. Highlighting the
 * raw trace therefore matched only the handful of symbol nodes that happened to
 * survive the export's node cap, and matches nothing at all now that the export
 * is file-only. Mapping each step onto its containing file highlights the route
 * the reader can actually see, and collapses consecutive steps within one file
 * so a 6-symbol trace through 2 files reads as 2 hops, not 6.
 */
export function traceToFileTrace(trace: readonly string[]): string[] {
  const files: string[] = [];
  for (const id of trace) {
    const file = id.split("::")[0] ?? id;
    if (files[files.length - 1] !== file) files.push(file);
  }
  return files;
}

export function traceToEdgeKeys(nodes: readonly string[]): Set<string> {
  const edgeKeys = new Set<string>();
  for (let i = 0; i < nodes.length - 1; i++) {
    edgeKeys.add(`${nodes[i]}→${nodes[i + 1]}`);
    edgeKeys.add(`${nodes[i + 1]}→${nodes[i]}`);
  }
  return edgeKeys;
}
