import {
  getRefactoringOpportunityPrompt,
  getRefactoringPlanPrompt,
} from "@repowise-dev/api-client/refactoring";
import {
  buildRefactoringOpportunityPrompt,
  buildRefactoringPlanPrompt,
  type AiPromptFlavor,
} from "@repowise-dev/ui/health/ai-prompt-builder";
import type {
  RefactoringOpportunityDetailResolved,
  RefactoringPlan,
} from "@repowise-dev/types/refactoring";
import type { RepowiseContext } from "./context";

/*
 * Core renders refactoring prompts on the server, so every surface hands an
 * agent the same text. The extension supports servers from MIN_SERVER_VERSION,
 * which predate the prompt routes: on a failed fetch it builds the prompt
 * locally instead. Drop the fallback (and the builders) once the floor passes
 * the release that serves the routes.
 */

async function served(
  ctx: RepowiseContext,
  load: (repoId: string) => Promise<{ text: string }>,
): Promise<string | null> {
  const repoId = ctx.repoId;
  if (!repoId) return null;
  try {
    return (await load(repoId)).text;
  } catch (err) {
    ctx.log.debug(`Server prompt unavailable, building it locally: ${String(err)}`);
    return null;
  }
}

function repoName(ctx: RepowiseContext): { repoName?: string } {
  return ctx.repo?.name ? { repoName: ctx.repo.name } : {};
}

/** One plan's agent prompt: the server's, else built here for an older server. */
export async function planPrompt(
  ctx: RepowiseContext,
  plan: RefactoringPlan,
  flavor: AiPromptFlavor,
): Promise<string> {
  const text = await served(ctx, (id) => getRefactoringPlanPrompt(id, plan.id, { flavor }));
  return text ?? buildRefactoringPlanPrompt({ plan, flavor, ...repoName(ctx) });
}

/** One opportunity's agent prompt; *detail* is read only for the fallback. */
export async function opportunityPrompt(
  ctx: RepowiseContext,
  opportunityId: string,
  flavor: AiPromptFlavor,
  detail: () => Promise<RefactoringOpportunityDetailResolved>,
): Promise<string> {
  const text = await served(ctx, (id) =>
    getRefactoringOpportunityPrompt(id, opportunityId, { flavor }),
  );
  if (text !== null) return text;
  return buildRefactoringOpportunityPrompt({ opportunity: await detail(), flavor, ...repoName(ctx) });
}
