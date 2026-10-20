/**
 * Agent prompts core renders: one action or Fix-first item as the text an agent
 * starts from. Mirrors `repowise.core.agent_prompts`;
 * `test_wire_vocabulary_parity.py` fails when the flavors disagree.
 */

/** The harness a prompt is worded for. */
export type AgentPromptFlavor = "generic" | "claude-code" | "claude-code-mcp" | "cursor";

/** `GET .../prompt?flavor=`: the flavor served and the rendered text. */
export type { AgentPromptResponse } from "./generated/http.js";
