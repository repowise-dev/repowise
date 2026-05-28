// Top-level script invoked through ``npm run build`` (``tsx scripts/build.ts``)
// — never imported by anything else but must not be flagged unreachable.
export function build(): void {
  console.log("build");
}

build();
