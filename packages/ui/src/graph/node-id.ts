import { nodeKind } from "@repowise-dev/types";

/** A bare graph node id that names a repository file: no `::symbol`, no `external:` style prefix. */
export function isFileNodeId(id: string): boolean {
  return nodeKind(id) === "path";
}
