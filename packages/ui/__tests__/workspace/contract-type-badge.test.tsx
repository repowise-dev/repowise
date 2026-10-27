import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  ContractTypeBadge,
  RoleBadge,
  contractTypeLabel,
} from "../../src/workspace/contract-type-badge.js";

describe("ContractTypeBadge", () => {
  it("names every type the extractors emit", () => {
    expect(contractTypeLabel("http")).toBe("HTTP");
    expect(contractTypeLabel("grpc")).toBe("gRPC");
    expect(contractTypeLabel("socket")).toBe("Socket");
    expect(contractTypeLabel("topic")).toBe("Topic");
    expect(contractTypeLabel("data")).toBe("Table");
    // The largest type in a real corpus, and the one that used to fall through
    // to the raw lowercase string.
    expect(contractTypeLabel("code")).toBe("Code");
  });

  it("falls back to the raw value for a type it does not know", () => {
    render(<ContractTypeBadge type="queue" />);
    expect(screen.getByText("queue")).toBeInTheDocument();
  });

  it("spends no ground or hue on the type", () => {
    // The demotion is the point: a tinted pill per type tiles into stripes
    // down a table, and following the colour goes nowhere.
    const { container } = render(<ContractTypeBadge type="http" />);
    const cls = container.firstElementChild?.className ?? "";
    expect(cls).not.toMatch(/\bbg-/);
    expect(cls).not.toMatch(/text-(green|yellow|amber|red|blue|purple|cyan|orange|teal)-/);
  });
});

describe("RoleBadge", () => {
  it("names the side without claiming a health band", () => {
    const { container } = render(<RoleBadge role="provider" />);
    expect(screen.getByText("Provider")).toBeInTheDocument();
    const cls = container.firstElementChild?.className ?? "";
    expect(cls).not.toMatch(/(green|yellow|amber|red)/);
  });

  it("treats anything that is not a provider as a consumer", () => {
    render(<RoleBadge role="consumer" />);
    expect(screen.getByText("Consumer")).toBeInTheDocument();
  });
});
