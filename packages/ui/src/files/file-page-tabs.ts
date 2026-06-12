/** Tab ids for the file entity page. Lives outside the client component so
 *  server components can validate `?tab=` values without importing client code. */
export const FILE_PAGE_TABS = ["doc", "health", "history", "coverage", "graph"] as const;
export type FilePageTab = (typeof FILE_PAGE_TABS)[number];
