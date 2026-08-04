// Statically inlined at build time via next.config.mjs's `env` key — safe
// to read from both server-rendered HTML and client components without a
// hydration mismatch, unlike a runtime process.env.GITHUB_PAGES check.
export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

export function withBasePath(path: string) {
  return `${BASE_PATH}${path}`;
}
