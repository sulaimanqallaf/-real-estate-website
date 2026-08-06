// Served from GitHub's default project-page URL
// (sulaimanqallaf.github.io/-real-estate-website), which needs every asset
// path prefixed with the repo name. Was briefly on a custom domain
// (altivaproperties.com, served from the root, no basePath) — reverted
// because that domain's DNS was never pointed at GitHub Pages, and GitHub
// Pages redirects the default URL to a configured custom domain even when
// that domain doesn't resolve, which made the site unreachable everywhere.
// public/CNAME was removed to stop that redirect from being re-asserted on
// every deploy. To switch back once DNS is sorted: delete this `env` block,
// re-add public/CNAME, and revert layout.tsx/robots.txt/sitemap.xml's URLs
// — src/lib/basePath.ts's withBasePath() needs no changes either way.
// basePath handles Next's own asset URLs (_next/static/...) and next/link's
// auto-prefixing; NEXT_PUBLIC_BASE_PATH (read via withBasePath()) covers
// everywhere else — plain <a>/<img> tags, which basePath does NOT touch.
// A <Link href={withBasePath(...)}> would get double-prefixed, since Link
// already applies basePath itself — see Projects.tsx/ProjectDetail.tsx,
// the only two places using next/link, which pass raw (unprefixed) paths.
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",
  basePath: "/-real-estate-website",
  images: {
    unoptimized: true,
  },
  env: {
    NEXT_PUBLIC_BASE_PATH: "/-real-estate-website",
  },
};

export default nextConfig;
