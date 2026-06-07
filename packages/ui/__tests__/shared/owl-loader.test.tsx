import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { OwlLoader } from "../../src/shared/owl-loader.js";

// Capture the loadError handler the component registers so tests can
// trigger the fallback path without a real Lottie runtime.
const listeners: Record<string, () => void> = {};
vi.mock("@lottiefiles/dotlottie-react", () => ({
  DotLottieReact: ({
    src,
    style,
    dotLottieRefCallback,
  }: {
    src: string;
    style?: Record<string, unknown>;
    dotLottieRefCallback?: (dotLottie: unknown) => void;
  }) => {
    dotLottieRefCallback?.({
      addEventListener: (event: string, handler: () => void) => {
        listeners[event] = handler;
      },
    });
    return <div data-testid="lottie" data-src={src} style={style} />;
  },
}));

describe("OwlLoader", () => {
  it("renders the animation with the default asset path and label", () => {
    render(<OwlLoader />);

    expect(screen.getByRole("status", { name: "Loading…" })).toBeInTheDocument();
    expect(screen.getByTestId("lottie")).toHaveAttribute(
      "data-src",
      "/owl-loading.json",
    );
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("passes a custom asset path and size through to the animation", () => {
    render(<OwlLoader src="/assets/owl.json" size={80} label="Crunching…" />);

    const lottie = screen.getByTestId("lottie");
    expect(lottie).toHaveAttribute("data-src", "/assets/owl.json");
    expect(lottie).toHaveStyle({ width: "80px", height: "80px" });
    expect(screen.getByRole("status", { name: "Crunching…" })).toBeInTheDocument();
  });

  it("falls back to the brand mark when the animation fails to load", async () => {
    render(
      <OwlLoader logoDarkSrc="/logo-dark.png" logoLightSrc="/logo-light.png" />,
    );

    listeners.loadError?.();

    await waitFor(() => {
      expect(screen.queryByTestId("lottie")).not.toBeInTheDocument();
    });
    // BrandMark renders both theme variants; visibility is CSS-only.
    const imgs = document.querySelectorAll("img");
    const srcs = Array.from(imgs).map((img) => img.getAttribute("src"));
    expect(srcs).toContain("/logo-light.png");
    expect(srcs).toContain("/logo-dark.png");
    // The loading state stays announced even on the fallback path.
    expect(screen.getByRole("status", { name: "Loading…" })).toBeInTheDocument();
  });
});
