/**
 * Engine events: what moved in a component between two consecutive index
 * runs under the same analyzer stamp, as CloudEvents 1.0.2 structured JSON.
 *
 * Generated from the server's response models
 * (`packages/server/src/repowise/server/schemas/events.py`). `type` is a
 * versioned `dev.repowise.*` string; `data` carries OTel `vcs.*` names for
 * the revisions and `repowise.*` keys for kind, component, severity and stamp.
 */
export type { CloudEvent, EventListResponse } from "./generated/http.js";
