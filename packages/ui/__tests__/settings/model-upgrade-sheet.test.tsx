import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  HOSTED_SOURCE_ID,
  PASTE_SOURCE_ID,
  defaultModelSource,
  orderModelSources,
  pasteKeyProviders,
  type ModelSourceProvider,
} from "../../src/settings/model-sources";
import { ModelUpgradeSheet, type ModelUpgradeSheetProps } from "../../src/settings/model-upgrade-sheet";

const CATALOG: ModelSourceProvider[] = [
  { id: "gemini", name: "Google Gemini", requires_key: true, detected: null },
  { id: "anthropic", name: "Anthropic", requires_key: true, detected: "saved" },
  { id: "openai", name: "OpenAI", requires_key: true, detected: "env" },
  { id: "ollama", name: "Ollama (Local)", requires_key: false, local_model: true, detected: null },
  { id: "claude_cli", name: "Claude Code (Local CLI)", requires_key: false, detected: "signed_in" },
  { id: "codex_cli", name: "Codex (Local CLI)", requires_key: false, detected: null },
  { id: "litellm", name: "LiteLLM", requires_key: false, detected: null },
];

describe("orderModelSources", () => {
  it("puts sign-ins, then keys, then the local model, paste and hosted last", () => {
    const sources = orderModelSources(CATALOG);
    expect(sources.map((s) => s.id)).toEqual([
      "claude_cli",
      "anthropic",
      "openai",
      "ollama",
      PASTE_SOURCE_ID,
      HOSTED_SOURCE_ID,
    ]);
    expect(sources.map((s) => s.kind)).toEqual(["cli", "key", "key", "local", "paste", "hosted"]);
    expect(sources[0]).toMatchObject({ name: "Claude Code", providerId: "claude_cli" });
    expect(sources[3]).toMatchObject({ name: "Ollama", detected: null });
  });

  it("leaves out providers with nothing set up", () => {
    const ids = orderModelSources(CATALOG).map((s) => s.id);
    expect(ids).not.toContain("gemini");
    expect(ids).not.toContain("codex_cli");
    expect(ids).not.toContain("litellm");
  });

  it("still offers paste and hosted with nothing detected", () => {
    expect(orderModelSources([]).map((s) => s.id)).toEqual([PASTE_SOURCE_ID, HOSTED_SOURCE_ID]);
  });
});

describe("defaultModelSource", () => {
  const sources = orderModelSources(CATALOG);

  it("keeps the repo's current provider when it is a row", () => {
    expect(defaultModelSource(sources, "openai")).toBe("openai");
  });

  it("falls back to the first detected source", () => {
    expect(defaultModelSource(sources, null)).toBe("claude_cli");
    expect(defaultModelSource(sources, "gemini")).toBe("claude_cli");
  });

  it("never defaults to the local model or hosted on its own", () => {
    const bare = orderModelSources([CATALOG[3]!]);
    expect(defaultModelSource(bare, null)).toBe(PASTE_SOURCE_ID);
  });
});

it("pasteKeyProviders offers every provider that takes a key", () => {
  expect(pasteKeyProviders(CATALOG).map((p) => p.id)).toEqual(["gemini", "anthropic", "openai"]);
});

function renderSheet(overrides: Partial<ModelUpgradeSheetProps> = {}) {
  const props: ModelUpgradeSheetProps = {
    open: true,
    onOpenChange: vi.fn(),
    sources: orderModelSources(CATALOG),
    selectedId: "claude_cli",
    onSelect: vi.fn(),
    keyProviders: [],
    onAddKey: vi.fn(),
    estimate: { pages: 46, costText: null, loading: false },
    onWrite: vi.fn(),
    hostedHref: "https://example.test/hosted",
    ...overrides,
  };
  render(<ModelUpgradeSheet {...props} />);
  return props;
}

describe("ModelUpgradeSheet", () => {
  it("names a sign-in as a sign-in, never as free", () => {
    renderSheet();
    expect(screen.getByText("Uses your Claude Code sign-in.")).toBeInTheDocument();
    expect(screen.getByText("46 pages to write")).toBeInTheDocument();
    expect(screen.queryByText(/free/i)).toBeNull();
    expect(screen.getByText("Found")).toBeInTheDocument();
    expect(screen.getByText("In env")).toBeInTheDocument();
  });

  it("selects a source and writes with it", () => {
    const props = renderSheet();
    fireEvent.click(screen.getByRole("radio", { name: /OpenAI key/ }));
    expect(props.onSelect).toHaveBeenCalledWith("openai");
    fireEvent.click(screen.getByRole("button", { name: "Write the docs" }));
    expect(props.onWrite).toHaveBeenCalled();
  });

  it("shows the key's price before anything runs", () => {
    renderSheet({ selectedId: "openai", estimate: { pages: 46, costText: "$0.42", loading: false } });
    expect(screen.getByText(/About \$0\.42 with this key/)).toBeInTheDocument();
  });

  it("links out for hosted instead of offering it as a radio", () => {
    renderSheet();
    expect(screen.getAllByRole("radio")).toHaveLength(5);
    const hosted = screen.getByRole("link", { name: /Hosted repowise/ });
    expect(hosted).toHaveAttribute("href", "https://example.test/hosted");
  });

  it("expands the key entry for Paste a key and waits for a key", () => {
    renderSheet({
      selectedId: PASTE_SOURCE_ID,
      keyProviders: [{ id: "anthropic", name: "Anthropic", models: [], default_model: "", configured: false }],
    });
    expect(screen.getByLabelText("Anthropic API key")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Write the docs" })).toBeDisabled();
  });
});
