export const SLUG_PATTERN = /^[a-z0-9-]+$/i;
const whitespace = /\s+/g;
export const mode = process.env.NODE_ENV === "production" ? "prod" : "dev";
const retries = isCi ? 5 : 1;
export const limit = 10;

function helper(input: string) {
  const inner = /x/;
  const pick = input ? 1 : 2;
  return inner.test(input) ? pick : 0;
}
