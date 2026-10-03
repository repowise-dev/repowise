/**
 * The pane's Ask tab and the `/lens` arguments. Pure: a question is routed to
 * `get_why` or `get_answer`, and the reply is kept as the tool sent it.
 */

export type PaneTab = "map" | "ask" | "recap";

export type AskTool = "get_why" | "get_answer";

// Typed here, not shared: the MCP server has no generated TypeScript types,
// and the shared chat types lack the evidence fields Lens shows.

export interface EvidenceRef {
  id?: string;
}

/** The fields of a `get_answer` reply the tab shows, as the server names them. */
export interface AnswerReply {
  answer?: string;
  confidence?: string;
  retrieval_quality?: string;
  citations?: string[];
  /** Why the answer is not a synthesis (`no-llm-provider`); absent when it may be one. */
  degraded?: string;
}

export interface WhyCommit {
  sha?: string;
  message?: string;
  date?: string;
  evidence_refs?: EvidenceRef[];
}

export interface WhyDecision {
  id?: string;
  title: string;
  status?: string;
  /** `accepted` when somebody signed it, `candidate` when nobody has yet. */
  authority?: string;
  confidence?: number | null;
  decision?: string;
}

export interface WhyRationale {
  path: string;
  lines?: [number, number];
  comment: string;
  evidence_refs?: EvidenceRef[];
}

/** The fields of a `get_why` reply the tab shows, as the server names them. */
export interface WhyReply {
  reason?: string;
  answer_basis?: string;
  decisions?: WhyDecision[];
  git_archaeology?: { summary?: string; git_log?: WhyCommit[]; file_commits?: WhyCommit[] };
  code_rationale?: WhyRationale[];
  code_rationale_total?: number;
}

export type AskReply = { tool: "get_answer"; reply: AnswerReply } | { tool: "get_why"; reply: WhyReply };

export type AskState =
  | { phase: "idle" }
  | { phase: "asking"; question: string; tool: AskTool }
  | { phase: "answered"; question: string; answer: AskReply }
  | { phase: "failed"; question: string; tool: AskTool; message: string };

/** A "why" question goes to the decision records; anything else to the index's answer. */
export function askRoute(question: string): { tool: AskTool; args: Record<string, unknown> } {
  return /^\s*why\b/i.test(question)
    ? { tool: "get_why", args: { query: question } }
    : { tool: "get_answer", args: { question } };
}

export function askReply(tool: AskTool, reply: unknown): AskReply {
  const body = typeof reply === "object" && reply !== null ? reply : {};
  return tool === "get_why" ? { tool, reply: body as WhyReply } : { tool, reply: body as AnswerReply };
}

/**
 * Whether a reply may have come from the index's own model: `get_answer`
 * synthesizes with one when the repo configures it. Only an answer that says
 * `no-llm-provider` is known to have used none.
 */
export function mayHaveUsedModel(a: AskReply): boolean {
  return a.tool === "get_answer" && a.reply.degraded !== "no-llm-provider";
}

/** What `/lens <args>` asks for: a tab to show, and a question to ask. */
export function lensCommand(args: string): { tab: PaneTab | null; question: string | null } {
  const [word = "", ...rest] = args.trim().split(/\s+/);
  const question = rest.join(" ");
  switch (word.toLowerCase()) {
    case "map":
    case "recap":
      return { tab: word.toLowerCase() as PaneTab, question: null };
    case "ask":
      return { tab: "ask", question: question === "" ? null : question };
    default:
      return { tab: null, question: null };
  }
}

/** The Ask field's text after `Why` on a review: the decision that governs the edit. */
export function whyDraft(decision: string): string {
  return `why ${decision}`;
}
