import type { FirstLookRouteKey } from "@repowise-dev/types/first-look";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";

/**
 * Where each first-look route key opens in this app, under `prefix`
 * (`/repos/{id}`). Every target is a page or tab that exists here; the
 * hosted app maps the same keys to its own routes.
 */
export function firstLookHref(
  prefix: string,
  route: FirstLookRouteKey,
  params: Record<string, string | number>,
): string | null {
  const path = typeof params.path === "string" ? params.path : null;
  switch (route) {
    case "file":
      if (!path) return null;
      return params.line ? `${fileEntityPath(prefix, path)}?line=${params.line}` : fileEntityPath(prefix, path);
    case "security":
      return `${prefix}/security`;
    case "dead_code":
      return `${prefix}/code-health?tab=dead-code`;
    case "health_file":
      return path ? `${prefix}/code-health?file=${encodeURIComponent(path)}` : `${prefix}/code-health`;
    case "docs_drift":
      return `${prefix}/docs/drift`;
    case "decision":
      return params.id ? `${prefix}/decisions/${encodeURIComponent(String(params.id))}` : `${prefix}/decisions`;
    case "ownership":
      return `${prefix}/ownership`;
    default:
      return null;
  }
}
