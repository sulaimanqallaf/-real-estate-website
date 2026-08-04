// GitHub Pages serves this as a project page at /<repo>/, so asset and
// route URLs need that prefix — but only for the Pages build, not local
// dev/Vercel (which serve from the domain root).
const isGithubPages = process.env.GITHUB_PAGES === "true";
const basePath = "/-real-estate-website";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",
  images: {
    unoptimized: true,
  },
  // `env` values get statically inlined at build time into every bundle —
  // server-rendered HTML and the client bundle read the same baked-in
  // string, so there's no risk of a hydration mismatch the way a runtime
  // process.env read in a "use client" component would have. next/image's
  // own basePath handling doesn't reliably kick in with unoptimized:true,
  // so components that reference public/ assets read this directly.
  env: {
    NEXT_PUBLIC_BASE_PATH: isGithubPages ? basePath : "",
  },
  ...(isGithubPages && {
    basePath,
    assetPrefix: basePath,
  }),
};

export default nextConfig;
