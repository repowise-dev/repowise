import { describe, expect, it } from "vitest";
import { coverageCommandForLanguages } from "./tests-view";

describe("coverageCommandForLanguages", () => {
  it("returns Go test and coverage add commands for Go repos", () => {
    const cmd = coverageCommandForLanguages([{ language: "Go" }]);
    expect(cmd).toBe("go test -coverprofile=coverage.out ./...\nrepowise coverage add coverage.out");
  });

  it("returns npm test and coverage add commands for TypeScript repos", () => {
    const cmd = coverageCommandForLanguages([{ language: "TypeScript" }, { language: "JavaScript" }]);
    expect(cmd).toBe("npm test -- --coverage\nrepowise coverage add coverage/lcov.info");
  });

  it("returns npm test and coverage add commands for JavaScript repos", () => {
    const cmd = coverageCommandForLanguages([{ language: "javascript" }]);
    expect(cmd).toBe("npm test -- --coverage\nrepowise coverage add coverage/lcov.info");
  });

  it("returns pytest and coverage add commands for Python repos", () => {
    const cmd = coverageCommandForLanguages([{ language: "python" }]);
    expect(cmd).toBe("pytest --cov --cov-report=lcov\nrepowise coverage add coverage.lcov");
  });

  it("returns cargo llvm-cov commands for Rust repos", () => {
    const cmd = coverageCommandForLanguages([{ language: "Rust" }]);
    expect(cmd).toBe("cargo llvm-cov --lcov --output-path coverage/lcov.info\nrepowise coverage add coverage/lcov.info");
  });

  it("returns mvn test jacoco commands for Java repos", () => {
    const cmd = coverageCommandForLanguages([{ language: "Java" }]);
    expect(cmd).toBe("mvn test jacoco:report\nrepowise coverage add target/site/jacoco/jacoco.xml");
  });

  it("returns mvn test jacoco commands for Kotlin repos", () => {
    const cmd = coverageCommandForLanguages([{ language: "Kotlin" }]);
    expect(cmd).toBe("mvn test jacoco:report\nrepowise coverage add target/site/jacoco/jacoco.xml");
  });

  it("returns undefined for empty, unknown, or missing languages to trigger the language-neutral default", () => {
    expect(coverageCommandForLanguages(undefined)).toBeUndefined();
    expect(coverageCommandForLanguages([])).toBeUndefined();
    expect(coverageCommandForLanguages([{ language: "brainfuck" }])).toBeUndefined();
  });
});
