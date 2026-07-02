import * as vscode from "vscode";
import { getFileDetail } from "@repowise-dev/api-client/files";
import { Commands } from "../constants";
import type { RepowiseContext } from "../core/context";
import { repoRelativePath } from "../core/fileSignals";

/** Virtual-document scheme for the rendered wiki page preview. */
const DOCS_SCHEME = "repowise-docs";

/**
 * Docs command: renders the wiki page for the active editor's file. The page
 * body ships inline on the file-detail aggregate, so one request maps a
 * repo-relative path to its content. The rendered markdown is cached per file
 * under the head-commit tag and shown as a markdown preview.
 */
export function registerDocs(ctx: RepowiseContext): vscode.Disposable {
  // Rendered page markdown, keyed by virtual-doc uri, for this session.
  const docContent = new Map<string, string>();
  const contentProvider: vscode.TextDocumentContentProvider = {
    provideTextDocumentContent: (uri) => docContent.get(uri.toString()) ?? "",
  };

  return vscode.Disposable.from(
    vscode.workspace.registerTextDocumentContentProvider(DOCS_SCHEME, contentProvider),
    vscode.commands.registerCommand(Commands.openDocs, () => openDocs(ctx, docContent)),
  );
}

async function openDocs(
  ctx: RepowiseContext,
  docContent: Map<string, string>,
): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    void vscode.window.showInformationMessage("Open a file to see its Repowise docs.");
    return;
  }

  const relPath = repoRelativePath(ctx, editor.document.uri);
  if (!relPath) {
    void vscode.window.showInformationMessage(
      "This file is not part of the indexed repository.",
    );
    return;
  }

  if (ctx.getExtensionState() !== "ready" || !ctx.repoId) {
    void vscode.window.showInformationMessage(
      "Start the Repowise server to load docs for this file.",
    );
    return;
  }

  const repoId = ctx.repoId;
  const tag = ctx.repo?.head_commit ?? "";
  const key = `docs:${relPath}`;

  let markdown = ctx.cache.get<string>(repoId, key, tag);
  if (markdown === undefined) {
    let rendered: string | null;
    try {
      const detail = await getFileDetail(repoId, relPath);
      rendered = detail.wiki_page ? renderPage(detail.wiki_page) : null;
    } catch (err) {
      ctx.log.error(`openDocs(${relPath}) failed: ${String(err)}`);
      void vscode.window.showErrorMessage(
        "Could not load docs for this file. See the Repowise log for details.",
      );
      return;
    }
    if (rendered === null) {
      void vscode.window.showInformationMessage("No docs indexed for this file.");
      return;
    }
    markdown = rendered;
    ctx.cache.set(repoId, key, tag, markdown);
  }

  // Tag the uri with the head commit so a reindex yields a fresh preview rather
  // than a stale cached render.
  const uri = vscode.Uri.from({
    scheme: DOCS_SCHEME,
    path: `/${relPath}.md`,
    query: tag,
  });
  docContent.set(uri.toString(), markdown);
  await vscode.commands.executeCommand("markdown.showPreview", uri);
}

/** Wiki page as markdown, with any human-curated note quoted above the body. */
function renderPage(page: {
  content: string;
  human_notes: string | null;
}): string {
  const notes = page.human_notes?.trim();
  if (!notes) return page.content;
  const quoted = notes
    .split("\n")
    .map((line) => `> ${line}`)
    .join("\n");
  return `${quoted}\n\n${page.content}`;
}
