"use client";

import useSWR from "swr";
import { getKnowledgeMatrix } from "@/lib/api/owners";

/** The one key every surface reads the knowledge matrix under, so it is fetched once. */
export function useKnowledgeMatrix(repoId: string, enabled = true) {
  return useSWR(enabled ? `knowledge-matrix:${repoId}` : null, () => getKnowledgeMatrix(repoId), {
    revalidateOnFocus: false,
  });
}
