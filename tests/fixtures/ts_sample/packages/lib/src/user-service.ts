import type { User } from "./types";

// Class implementing an interface via a field-typed annotation — exercises
// Phase 2's type_use edge resolution. ``User`` must NOT be flagged
// unused_export despite no value-position reference.
export class UserService {
  private current: User | null = null;

  setCurrent(user: User): void {
    this.current = user;
  }

  greet(): string {
    return this.current?.name ?? "anonymous";
  }
}
