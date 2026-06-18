/** Tab ids for the file entity page. Lives outside the client component so
 *  server components can validate `?tab=` values without importing client code.
 *  Order is the rendered tab order: Overview first so undocumented files are
 *  never empty; Coverage last (often empty). */
export const FILE_PAGE_TABS = [
  "overview",
  "doc",
  "health",
  "history",
  "graph",
  "coverage",
] as const;
export type FilePageTab = (typeof FILE_PAGE_TABS)[number];
