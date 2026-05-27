/**
 * Pure helpers extracted from GraphFlow to remove duplication.
 */

/**
 * Build the set of directed edge keys for a node trace/path, in both
 * directions. Used by both the execution-flow highlighter and the path-finder
 * result handler — they highlight the same way, so the key construction lives
 * here once. Mirrors Sigma's `source→target` edge-key convention.
 */
export function traceToEdgeKeys(nodes: readonly string[]): Set<string> {
  const edgeKeys = new Set<string>();
  for (let i = 0; i < nodes.length - 1; i++) {
    edgeKeys.add(`${nodes[i]}→${nodes[i + 1]}`);
    edgeKeys.add(`${nodes[i + 1]}→${nodes[i]}`);
  }
  return edgeKeys;
}
