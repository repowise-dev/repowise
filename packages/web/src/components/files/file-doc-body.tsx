"use client";

import { useCallback } from "react";
import Link from "next/link";
import { WikiMarkdown } from "@repowise-dev/ui/wiki/wiki-markdown";
import { usePages } from "@/lib/hooks/use-pages";
import { pageHref } from "@/lib/utils/page-href";

/**
 * The wiki body on the file route's Documentation tab.
 *
 * It rendered with no page list, so every backticked path in it was dead text
 * on the one surface where the reader is already looking at a file. Resolving
 * them needs `buildHref`, and a function cannot cross into a client component
 * from a server one, which is what this wrapper is for.
 *
 * The list is fetched here rather than in the route because it is repo-wide:
 * server-side it would ride the flight payload of every file page, where the
 * shared SWR key is one fetch per session and is already warm for anyone who
 * opened the wiki. The tab mounts only when it is selected, and until the list
 * lands the paths render as plain code, exactly as they did before.
 */
export function FileDocBody({ repoId, content }: { repoId: string; content: string }) {
  const { pages } = usePages(repoId);

  // `pageHref`, not `fileEntityPath`: the path index also resolves a directory
  // ref to its module page, and the file route 404s on a directory. Routing by
  // id prefix sends those to the docs reader instead of to a dead link.
  const buildHref = useCallback((pageId: string) => pageHref(repoId, pageId), [repoId]);

  return (
    <WikiMarkdown
      content={content}
      pages={pages}
      buildHref={buildHref}
      LinkComponent={Link}
    />
  );
}
