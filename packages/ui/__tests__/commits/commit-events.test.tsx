import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { CloudEvent } from "@repowise-dev/types/events";
import { CommitEvents } from "../../src/commits/commit-events.js";

function event(id: string, kind: string, severity: string, component = ""): CloudEvent {
  return {
    id,
    data: { "repowise.kind": kind, "repowise.severity": severity, "repowise.component": component },
  } as unknown as CloudEvent;
}

describe("CommitEvents", () => {
  it("renders nothing for a quiet commit", () => {
    const { container } = render(<CommitEvents events={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("names each event, its component and its severity in words", () => {
    render(
      <CommitEvents
        events={[event("1", "contract_broken", "high", "server"), event("2", "test_reach_drop", "info")]}
      />,
    );
    expect(screen.getByText("Contract with consumers disappeared")).toBeInTheDocument();
    expect(screen.getByText("server")).toBeInTheDocument();
    expect(screen.getByText("high")).toBeInTheDocument();
    // No component: the whole repository; info carries no severity mark.
    expect(screen.getByText("Whole repository")).toBeInTheDocument();
    expect(screen.queryByText("info")).toBeNull();
  });

  it("takes the host's words", () => {
    render(
      <CommitEvents
        events={[event("1", "bus_factor_drop", "low", "core")]}
        labels={{ title: "T", kind: () => "K", severity: () => "S" }}
      />,
    );
    expect(screen.getByText("T")).toBeInTheDocument();
    expect(screen.getByText("K")).toBeInTheDocument();
    expect(screen.getByText("S")).toBeInTheDocument();
  });
});
