"use client";

/**
 * Back-compat shim. The renderer moved to `shared/markdown` once decisions
 * needed it too — it was never chat-specific, and leaving it under `chat/`
 * invited a fourth copy for the next surface that wanted themed markdown.
 *
 * This path stays because the hosted frontend imports
 * `@repowise-dev/ui/chat/chat-markdown` directly.
 */

export {
  Markdown,
  ChatMarkdown,
  type MarkdownProps,
  type MarkdownDensity,
  type ChatMarkdownDensity,
} from "../shared/markdown";
