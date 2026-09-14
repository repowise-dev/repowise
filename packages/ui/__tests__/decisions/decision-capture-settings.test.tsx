import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { DecisionCaptureSettings } from "../../src/decisions/decision-capture-settings";
import type {
  DecisionSettings,
  DecisionSourceState,
} from "@repowise-dev/types/decisions";

function source(overrides: Partial<DecisionSourceState> = {}): DecisionSourceState {
  return {
    key: "pr",
    label: "Pull request bodies",
    description: "Squash-merge and PR bodies that read as a description.",
    authority: "machine",
    deterministic: false,
    supports_llm: true,
    togglable: true,
    enabled: true,
    llm_enabled: true,
    status: "enabled",
    reason: "",
    ...overrides,
  };
}

function settings(overrides: Partial<DecisionSettings> = {}): DecisionSettings {
  return {
    enabled: true,
    llm: true,
    preset: "default",
    discovery: { max_sessions: 12, max_input_tokens: 30000 },
    sources: [source()],
    provider_available: true,
    warnings: [],
    legacy_keys: [],
    etag: "etag-1",
    ...overrides,
  };
}

function renderPanel(
  props: Partial<React.ComponentProps<typeof DecisionCaptureSettings>> = {},
) {
  const onChange = vi.fn(async () => undefined);
  render(
    <DecisionCaptureSettings
      settings={settings()}
      onChange={onChange}
      {...props}
    />,
  );
  return onChange;
}

describe("the panel is the registry, not a copy of it", () => {
  it("renders a row per source the payload carries, whatever it names", () => {
    renderPanel({
      settings: settings({
        sources: [
          source({ key: "adr", label: "ADR files" }),
          source({ key: "brand_new", label: "Something added later" }),
        ],
      }),
    });

    expect(screen.getByText("ADR files")).toBeInTheDocument();
    expect(screen.getByText("Something added later")).toBeInTheDocument();
    expect(screen.getByText("brand_new")).toBeInTheDocument();
  });

  it("shows the engine's own effective status and reason", () => {
    renderPanel({
      settings: settings({
        sources: [
          source({
            status: "skipped_no_provider",
            reason: "no model key is configured",
          }),
        ],
      }),
    });

    expect(
      screen.getByText(/skipped no provider · no model key is configured/),
    ).toBeInTheDocument();
  });
});

describe("writes carry the etag", () => {
  it("sends the etag with every switch, not only the risky ones", async () => {
    const onChange = renderPanel();

    fireEvent.click(screen.getByLabelText("Capture decisions"));

    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith({ enabled: false, etag: "etag-1" }),
    );
  });

  it("patches one source rather than the whole list", async () => {
    const onChange = renderPanel();
    const row = screen.getByText("Pull request bodies").closest("li");

    fireEvent.click(within(row as HTMLElement).getByLabelText("Model"));

    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith({
        sources: { pr: { llm: false } },
        etag: "etag-1",
      }),
    );
  });

  it("says a concurrent edit was refused rather than overwritten", async () => {
    const onChange = vi.fn(async () => {
      throw new Error("409: the policy changed");
    });
    render(<DecisionCaptureSettings settings={settings()} onChange={onChange} />);

    fireEvent.click(screen.getByLabelText("Capture decisions"));

    await waitFor(() =>
      expect(screen.getByText(/changed somewhere else/)).toBeInTheDocument(),
    );
  });
});

describe("a control that cannot act says why before it is pressed", () => {
  it("disables the model switch on a source that has no model stage", () => {
    renderPanel({
      settings: settings({ sources: [source({ supports_llm: false })] }),
    });
    const row = screen.getByText("Pull request bodies").closest("li");
    const toggle = within(row as HTMLElement).getByLabelText("Model");

    expect(toggle).toBeDisabled();
    expect(toggle).toHaveAttribute("title", "This source has no model stage.");
  });

  it("disables the capture switch on the authority route", () => {
    renderPanel({
      settings: settings({
        sources: [
          source({
            key: "cli",
            label: "Manual entry",
            authority: "human",
            togglable: false,
            supports_llm: false,
            status: "always_on",
          }),
        ],
      }),
    });
    const row = screen.getByText("Manual entry").closest("li");
    const toggle = within(row as HTMLElement).getByLabelText("Capture");

    expect(toggle).toBeDisabled();
    expect(toggle.getAttribute("title")).toMatch(/Always available/);
  });

  it("disables every source switch while capture is off, and says so", () => {
    renderPanel({ settings: settings({ enabled: false }) });
    const row = screen.getByText("Pull request bodies").closest("li");

    for (const name of ["Capture", "Model"]) {
      const toggle = within(row as HTMLElement).getByLabelText(name);
      expect(toggle).toBeDisabled();
      expect(toggle).toHaveAttribute("title", "Capture is switched off.");
    }
  });

  it("disables a source's model switch while the master model switch is off", () => {
    renderPanel({ settings: settings({ llm: false }) });
    const row = screen.getByText("Pull request bodies").closest("li");

    expect(within(row as HTMLElement).getByLabelText("Model")).toHaveAttribute(
      "title",
      "Model calls are switched off.",
    );
    // Capture is a different axis and stays usable.
    expect(within(row as HTMLElement).getByLabelText("Capture")).toBeEnabled();
  });
});

describe("a surface that cannot write says so and points at the CLI", () => {
  it("locks every control and names the route", () => {
    const onChange = renderPanel({ readOnlyReason: "Read-only snapshot." });

    expect(screen.getByLabelText("Capture decisions")).toBeDisabled();
    expect(screen.getByText(/repowise decision config show/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "balanced" }));
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("state the payload reports", () => {
  it("says the provider is absent rather than painting it a warning", () => {
    renderPanel({ settings: settings({ provider_available: false }) });

    expect(screen.getByText(/No provider is configured/)).toBeInTheDocument();
  });

  it("surfaces resolver warnings verbatim", () => {
    renderPanel({
      settings: settings({ warnings: ["unknown source 'readme_mining' ignored"] }),
    });

    expect(
      screen.getByText("unknown source 'readme_mining' ignored"),
    ).toBeInTheDocument();
  });

  it("warns that saving replaces a legacy key", () => {
    renderPanel({ settings: settings({ legacy_keys: ["decisions.mine"] }) });

    expect(screen.getByText("decisions.mine")).toBeInTheDocument();
    expect(screen.getByText(/replaces it/)).toBeInTheDocument();
  });

  it("shows custom as a reading and never as a preset to pick", () => {
    renderPanel({ settings: settings({ preset: "custom" }) });

    // `custom` is a description of where the policy is, so it renders as a
    // word beside the presets and never as a sixth one to press. The section's
    // own prose names it too, hence the getAll.
    expect(
      screen.queryByRole("button", { name: "custom" }),
    ).not.toBeInTheDocument();
    expect(screen.getAllByText("custom").length).toBeGreaterThan(0);
  });

  it("marks the active preset for assistive technology, not only in colour", () => {
    renderPanel({ settings: settings({ preset: "balanced" }) });

    expect(screen.getByRole("button", { name: "balanced" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "full" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("states the discovery budget with its units", () => {
    renderPanel();

    expect(screen.getByText("Sessions per update")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("30,000")).toBeInTheDocument();
  });
});

describe("loading and failure", () => {
  it("renders nothing while the first fetch is in flight", () => {
    const { container } = render(
      <DecisionCaptureSettings
        settings={undefined}
        onChange={vi.fn(async () => undefined)}
        isLoading
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("offers a retry when the fetch failed", () => {
    render(
      <DecisionCaptureSettings
        settings={undefined}
        onChange={vi.fn(async () => undefined)}
        error={new Error("boom")}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByText(/Couldn't load capture settings/)).toBeInTheDocument();
  });
});
