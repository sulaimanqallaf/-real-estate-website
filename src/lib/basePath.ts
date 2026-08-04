// Currently always "" — the site is served from a custom domain's root
// (see next.config.mjs), so no base path is configured. Kept as a no-op
// rather than removed: if this ever moves back to a project-page-style
// deployment (github.io/<repo>/), wiring next.config.mjs's `env` key back
// to NEXT_PUBLIC_BASE_PATH is a one-line change, and every call site
// below already routes through withBasePath() instead of a literal "/".
export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

export function withBasePath(path: string) {
  return `${BASE_PATH}${path}`;
}
