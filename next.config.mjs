// Served from the custom domain (altivaproperties.com) at the root, via
// GitHub Pages' custom-domain feature — no basePath needed. (Previously
// this conditionally added a /-real-estate-website basePath for the
// default project-page URL; now that a custom domain owns the apex, that
// mode is retired. src/lib/basePath.ts's withBasePath() still works, it
// just always resolves to "" now.)
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",
  images: {
    unoptimized: true,
  },
};

export default nextConfig;
