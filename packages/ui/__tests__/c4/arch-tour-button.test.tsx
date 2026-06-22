/**
 * ArchTourButton — the promoted "Take the tour" trigger. Both apps mount it
 * unconditionally; it reads the curated tour off the shared architecture store
 * and degrades to nothing when there are no steps (or while a tour is running).
 */
import { describe, it, expect, beforeEach } from "vitest";
import { act, render, fireEvent } from "@testing-library/react";
import { ArchTourButton } from "../../src/c4/panels/ArchTourButton";
import { useArchitectureStore } from "../../src/c4/store/use-architecture-store";
import { createMockView } from "./fixtures";
import type { ArchitectureView } from "../../src/c4/types";

const store = useArchitectureStore;

beforeEach(() => {
  store.setState(store.getInitialState());
  localStorage.clear();
});

function viewWithoutTour(): ArchitectureView {
  return { ...createMockView(), tour: [] };
}

describe("ArchTourButton", () => {
  it("renders nothing before a view loads", () => {
    const { queryByText } = render(<ArchTourButton />);
    expect(queryByText("Take the tour")).toBeNull();
  });

  it("renders nothing when the view carries no tour steps", () => {
    act(() => {
      store.getState().setView(viewWithoutTour());
    });
    const { queryByText } = render(<ArchTourButton />);
    expect(queryByText("Take the tour")).toBeNull();
  });

  it("shows the trigger when the view has a tour and starts it on click", () => {
    act(() => {
      store.getState().setView(createMockView()); // fixture carries a tour
    });
    const { getByText, queryByText } = render(<ArchTourButton />);

    const trigger = getByText("Take the tour");
    expect(trigger).toBeDefined();

    fireEvent.click(trigger);

    // The store's tour player is now running and the trigger hides itself.
    expect(store.getState().tourActive).toBe(true);
    expect(queryByText("Take the tour")).toBeNull();
  });
});
