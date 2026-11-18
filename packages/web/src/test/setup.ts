import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// The web config does not enable `globals: true`, so Testing Library's
// auto-cleanup never registers. Register it here once for every test file;
// files that already call `afterEach(cleanup)` keep working, because a
// second cleanup over an empty container is a no-op.
afterEach(cleanup);
