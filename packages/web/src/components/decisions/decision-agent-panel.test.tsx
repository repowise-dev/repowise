// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import enMessages from "../../../messages/en.json";
import type { DecisionAgentPromptKind } from "@repowise-dev/types/decisions";

const prompts = vi.hoisted(() => ({
  discover: { kind: "discover", text: "Check these 3 records\n- `a1` One", ids: ["a1"], count: 3 },
  add: { kind: "add", text: "Record what nobody wrote down", ids: [], count: 0 },
  agreements: { kind: "agreements", text: "", ids: [], count: 0 },
  // `null`: the server refuses the kind, as one that predates it does.
  reviewAll: { kind: "review_all", text: "Check every background record", ids: [], count: 0 } as
    | { kind: string; text: string; ids: string[]; count: number }
    | null,
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock("@/lib/api/decisions", () => ({
  createDecision: vi.fn(),
  getDecisionAgentPrompt: vi.fn(async (_repo: string, kind: DecisionAgentPromptKind) => {
    if (kind === "discover") return prompts.discover;
    if (kind === "add") return prompts.add;
    if (kind === "review_all") {
      if (!prompts.reviewAll) throw new Error("422 Unprocessable Entity");
      return prompts.reviewAll;
    }
    return prompts.agreements;
  }),
}));

import { getDecisionAgentPrompt } from "@/lib/api/decisions";
import { DecisionAgentPanel } from "./add-decision-button";

function renderPanel() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale="en" messages={enMessages}>
        <DecisionAgentPanel repoId="r1" />
      </NextIntlClientProvider>
    </SWRConfig>,
  );
}

describe("DecisionAgentPanel", () => {
  afterEach(cleanup);

  it("leads with the server's discover prompt and copies it as returned", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderPanel();

    expect(
      await screen.findByRole("heading", { name: "Check 3 background records on the busiest files" }),
    ).toBeTruthy();
    // The add pass is offered beside it; agreements has no text, so no link.
    expect(await screen.findByRole("button", { name: "Record missed decisions" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Move working agreements to CLAUDE.md" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Copy prompt for your agent" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(prompts.discover.text));
  });

  it("offers the whole-queue prompt beside the main button and copies it", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "Review all records" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("Check every background record"));
  });

  it("hides the whole-queue button when the server refuses the kind or has nothing left", async () => {
    prompts.reviewAll = null;
    const refused = renderPanel();
    await screen.findByRole("button", { name: "Copy prompt for your agent" });
    // Asked, refused, and nothing shows for it.
    await waitFor(() =>
      expect(vi.mocked(getDecisionAgentPrompt)).toHaveBeenCalledWith("r1", "review_all", undefined),
    );
    // Let the rejection settle before checking that nothing shows for it.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(screen.queryByRole("button", { name: "Review all records" })).toBeNull();
    refused.unmount();

    prompts.reviewAll = { kind: "review_all", text: "", ids: [], count: 0 };
    renderPanel();
    await screen.findByRole("button", { name: "Copy prompt for your agent" });
    expect(screen.queryByRole("button", { name: "Review all records" })).toBeNull();
  });

  it("becomes the add pass on a store with nothing to check, and keeps the manual path", async () => {
    prompts.discover = { ...prompts.add };
    renderPanel();

    expect(
      await screen.findByRole("heading", { name: "Record the decisions this repository already follows" }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Record missed decisions" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Add one by hand" }));
    expect(await screen.findByRole("dialog")).toBeTruthy();
  });
});
