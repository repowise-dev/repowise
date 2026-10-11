import { apiGet } from "./client";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface KnowledgeMapOwner {
  email: string;
  name: string;
  files_owned: number;
  percentage: number;
}

export interface KnowledgeMapSilo {
  file_path: string;
  owner_email: string;
  owner_pct: number;
}

export interface KnowledgeMapTarget {
  path: string;
  pagerank: number;
  doc_words: number;
}

export interface KnowledgeMapResponse {
  top_owners: KnowledgeMapOwner[];
  knowledge_silos: KnowledgeMapSilo[];
  onboarding_targets: KnowledgeMapTarget[];
}

// ---------------------------------------------------------------------------
// API call
// ---------------------------------------------------------------------------

export async function getKnowledgeMap(repoId: string): Promise<KnowledgeMapResponse> {
  return apiGet<KnowledgeMapResponse>(`/api/repos/${repoId}/knowledge-map`);
}
