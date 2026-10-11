// Genuinely-orphaned module: not in any package.json#exports map, not
// imported by anyone, no never-flag glob covers it. Must BE flagged
// unreachable_file — the analyzer's true-positive cross-check.
export function unusedHelper(): number {
  return 42;
}
