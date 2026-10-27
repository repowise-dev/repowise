// Recorded from `repowise serve` 0.54.0 on a copy of test-repos/django (commit
// e789914): GET /health/map?cap=4000 and POST /blast-radius for
// django/db/models/query.py at max_depth 1, each cut to the fields Lens reads.
import type { HealthMapFeed } from "@repowise-dev/types/health";
import { fixture } from "./fake-host";

export const feed = JSON.parse(fixture("django-health-map.json")) as HealthMapFeed;
export const importers = (
  JSON.parse(fixture("django-blast-radius-query-depth1.json")) as { transitive_affected: { path: string }[] }
).transitive_affected.map((t) => t.path);

export function decode(cells: string): Uint32Array {
  const bytes = Buffer.from(cells, "base64");
  return new Uint32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 4);
}
