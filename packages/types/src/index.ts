/**
 * @repowise/types — canonical TypeScript data contracts shared between
 * `packages/web` (OSS dashboard) and the hosted web app (currently `frontend/`,
 * to be moved to `packages/hosted-web/` in Phase 1B per Option α).
 *
 * Re-exports per-domain modules. Subpath imports (`@repowise/types/graph`)
 * are also supported via the `exports` map in package.json.
 */

export * from "./graph.js";
export * from "./git.js";
export * from "./docs.js";
export * from "./decisions.js";
export * from "./dead-code.js";
export * from "./symbols.js";
export * from "./chat.js";
