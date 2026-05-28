// Next.js App Router page — never imported by static code; reachability
// flows through Phase 3's next_app framework edges and the
// ``**/page.tsx`` never-flag glob.
import { helper } from "../helper";

export default function Page(): string {
  return helper();
}
