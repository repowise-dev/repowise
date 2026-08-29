import type { ChatContext, ChatContextKind } from "@repowise-dev/types/chat";

export type {
  ChatContext,
  ChatContextKind,
  ChatContextTargetKind,
} from "@repowise-dev/types/chat";

export interface ChatContextPresentation {
  placeholder: string;
  suggestions: readonly string[];
}

const PRESENTATIONS: Record<ChatContextKind, ChatContextPresentation> = {
  repository: {
    placeholder: "Ask about this repository, or paste a file path",
    suggestions: [
      "Give me an overview of this codebase",
      "What are the highest-risk files to modify?",
      "What architectural decisions have been made?",
      "What dead code can be safely removed?",
    ],
  },
  overview: {
    placeholder: "Ask about this repository overview",
    suggestions: [
      "Explain the main architectural boundaries",
      "Which parts of this repository deserve attention first?",
      "Where should a new contributor start?",
    ],
  },
  documentation: {
    placeholder: "Ask about the documentation on this page",
    suggestions: [
      "Explain this section using the source code",
      "Which source files support this documentation?",
      "What important details or limitations should I know?",
    ],
  },
  architecture: {
    placeholder: "Ask about this architecture view",
    suggestions: [
      "Explain the main component boundaries",
      "Which dependencies create the most coupling?",
      "Trace a request through this architecture",
    ],
  },
  graph: {
    placeholder: "Ask about relationships in this graph",
    suggestions: [
      "Explain the most important relationships shown here",
      "Which nodes have the widest structural impact?",
      "What is omitted or capped in this view?",
    ],
  },
  health: {
    placeholder: "Ask about these code health findings",
    suggestions: [
      "Which finding should I address first and why?",
      "Explain the evidence behind the highest-risk finding",
      "Propose a safe refactoring sequence",
    ],
  },
  refactoring: {
    placeholder: "Ask about this refactoring opportunity",
    suggestions: [
      "Turn this into a safe refactoring plan",
      "Which tests should protect this change?",
      "What could break if I modify these files?",
    ],
  },
  file: {
    placeholder: "Ask about this file",
    suggestions: [
      "Explain this file's responsibility",
      "Who calls into this file and what does it depend on?",
      "What is risky about changing this file?",
    ],
  },
  symbol: {
    placeholder: "Ask about this symbol",
    suggestions: [
      "Explain what this symbol does",
      "Show its callers and important dependencies",
      "What behavior should tests protect here?",
    ],
  },
  module: {
    placeholder: "Ask about this module",
    suggestions: [
      "Explain this module's responsibilities",
      "Which modules depend on it?",
      "Where are its highest-risk boundaries?",
    ],
  },
  commit: {
    placeholder: "Ask about this commit",
    suggestions: [
      "Summarize the intent and impact of this commit",
      "Which files in this change deserve the closest review?",
      "What tests should validate this change?",
    ],
  },
  contributor: {
    placeholder: "Ask about this contributor's ownership context",
    suggestions: [
      "Summarize this contributor's ownership areas",
      "Where is knowledge concentrated around their work?",
      "Which files have the lowest ownership resilience?",
    ],
  },
  decision: {
    placeholder: "Ask about this architectural decision",
    suggestions: [
      "Explain why this decision was made",
      "Show the evidence and affected code",
      "Has later work superseded or conflicted with it?",
    ],
  },
  risk: {
    placeholder: "Ask about the risk evidence on this page",
    suggestions: [
      "Explain the highest-risk result in plain language",
      "Which tests reduce the most uncertainty?",
      "Propose the safest order for these changes",
    ],
  },
  security: {
    placeholder: "Ask about these security findings",
    suggestions: [
      "Which security finding should be investigated first?",
      "Explain the evidence without overstating certainty",
      "Which code paths are affected?",
    ],
  },
  usage: {
    placeholder: "Ask about usage and savings",
    suggestions: [
      "Explain the largest source of usage",
      "Which figures are measured versus estimated?",
      "Where could usage be reduced safely?",
    ],
  },
  settings: {
    placeholder: "Ask about this repository configuration",
    suggestions: [
      "Explain the settings on this page",
      "Which settings affect indexing quality?",
      "What should I verify before changing this configuration?",
    ],
  },
  chat: {
    placeholder: "Ask a follow-up, or paste a file path",
    suggestions: [
      "Give me an overview of this codebase",
      "What are the highest-risk files to modify?",
      "Score the change risk of HEAD",
      "What architectural decisions have been made?",
    ],
  },
};

const COLLECTION_PRESENTATIONS: Partial<
  Record<ChatContextKind, ChatContextPresentation>
> = {
  documentation: {
    placeholder: "Ask about this repository's documentation",
    suggestions: [
      "Which documentation should I read first?",
      "Where is documentation missing or stale?",
      "Connect the documentation to its source files",
    ],
  },
  file: {
    placeholder: "Ask about files in this repository",
    suggestions: [
      "Which files are the main entry points?",
      "Find the files responsible for a feature",
      "Which files are riskiest to modify?",
    ],
  },
  symbol: {
    placeholder: "Ask about symbols in this repository",
    suggestions: [
      "Find the symbol responsible for a behavior",
      "Which symbols have the widest impact?",
      "Show the most important public interfaces",
    ],
  },
  module: {
    placeholder: "Ask about modules in this repository",
    suggestions: [
      "Explain the main module boundaries",
      "Which modules are most tightly coupled?",
      "Where should a new feature live?",
    ],
  },
  commit: {
    placeholder: "Ask about repository history",
    suggestions: [
      "Summarize the most important recent changes",
      "Which files change together most often?",
      "Find the history behind an architectural choice",
    ],
  },
  contributor: {
    placeholder: "Ask about ownership and contributors",
    suggestions: [
      "Where is repository knowledge concentrated?",
      "Which areas have the lowest ownership resilience?",
      "Who knows the highest-risk files best?",
    ],
  },
  decision: {
    placeholder: "Ask about architectural decisions",
    suggestions: [
      "Summarize the active architectural decisions",
      "Which decisions affect the most code?",
      "Find conflicting or superseded decisions",
    ],
  },
};

export function getChatContextPresentation(
  context?: ChatContext,
): ChatContextPresentation {
  if (context && !context.target) {
    const collectionPresentation = COLLECTION_PRESENTATIONS[context.kind];
    if (collectionPresentation) return collectionPresentation;
  }
  return PRESENTATIONS[context?.kind ?? "repository"];
}

const LEGACY_ARTIFACT_TYPE_BY_TOOL: Readonly<Record<string, string>> = {
  get_overview: "overview",
  get_context: "wiki_page",
  get_risk: "risk_report",
  get_change_risk: "risk_report",
  get_why: "decisions",
  search_codebase: "search_results",
  get_dead_code: "dead_code",
  get_dependency_path: "graph",
  get_architecture_diagram: "diagram",
};

/**
 * Restores artifact affordances for stored conversations whose legacy wire
 * shape persisted only the tool name and result. Unknown tools deliberately
 * use the generic renderer so evidence remains inspectable.
 */
export function getLegacyChatArtifactType(toolName: string): string {
  return LEGACY_ARTIFACT_TYPE_BY_TOOL[toolName] ?? "generic";
}
