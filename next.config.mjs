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
  ...(isGithubPages && {
    basePath,
    assetPrefix: basePath,
  }),
};

export default nextConfig;
