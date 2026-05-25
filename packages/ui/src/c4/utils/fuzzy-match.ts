import type { ArchNode, SearchResult } from "../types";

function scoreField(query: string, field: string): number {
  const lower = field.toLowerCase();
  let lastIndex = -1;
  let firstIndex = -1;
  let consecutiveBonus = 0;

  for (let i = 0; i < query.length; i++) {
    const char = query[i]!;
    const index = lower.indexOf(char, lastIndex + 1);
    if (index === -1) return 0;
    if (firstIndex === -1) firstIndex = index;
    if (index === lastIndex + 1) {
      consecutiveBonus += 0.1;
    }
    lastIndex = index;
  }

  const span = lastIndex - firstIndex + 1;
  let score = query.length / span;
  score += consecutiveBonus;

  if (firstIndex === 0) {
    score += 0.2;
  } else if (
    firstIndex > 0 &&
    (lower[firstIndex - 1] === " " || lower[firstIndex - 1] === "/" || lower[firstIndex - 1] === ".")
  ) {
    score += 0.1;
  }

  return Math.min(score, 1);
}

function fuzzyMatch(
  query: string,
  nodes: readonly ArchNode[],
  limit = 5,
): SearchResult[] {
  if (!query) return [];
  const lowerQuery = query.toLowerCase();

  const scored: SearchResult[] = [];

  for (const node of nodes) {
    const nameScore = scoreField(lowerQuery, node.name);
    const summaryScore = scoreField(lowerQuery, node.summary);
    const tagsScore = scoreField(lowerQuery, node.tags.join(" "));

    let bestScore = nameScore;
    let matchedField: "name" | "summary" | "tags" = "name";

    if (summaryScore > bestScore) {
      bestScore = summaryScore;
      matchedField = "summary";
    }
    if (tagsScore > bestScore) {
      bestScore = tagsScore;
      matchedField = "tags";
    }

    if (bestScore > 0) {
      scored.push({
        nodeId: node.id,
        name: node.name,
        node_type: node.node_type,
        score: bestScore,
        matchedField,
      });
    }
  }

  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, limit);
}

export { fuzzyMatch, scoreField };
