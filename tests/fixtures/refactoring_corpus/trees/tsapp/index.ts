// Archetype: a TypeScript re-export barrel.
//
// Every line is structurally identical to its neighbours, so a clone detector
// reads the file as wall-to-wall duplication. There is nothing to extract: the
// repetition *is* the module's content. The correct composed answer is silence,
// or at most evidence, never a step instructing a shared helper.

export { parseAccount } from "./account";
export { formatAccount } from "./account";
export { parseWorkspace } from "./workspace";
export { formatWorkspace } from "./workspace";
export type { Account } from "./account";
export type { Workspace } from "./workspace";
