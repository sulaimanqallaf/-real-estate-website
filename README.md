# Altiva Real Estate — Marketing Website

Bilingual (Arabic RTL / English LTR) marketing site for Altiva Real Estate, built with Next.js 14 (App Router), TypeScript, Tailwind CSS, and Framer Motion. Configured for static export (`output: "export"`) so it can be deployed on Vercel, GitHub Pages, or any static host.

**Live:** https://altivaproperties.com (custom domain via GitHub Pages — `public/CNAME` — auto-deployed via `.github/workflows/deploy-pages.yml` on every push to `main`)

## Development

```bash
npm install
npm run dev
```

## Production build (static export)

```bash
npm run build
```

Static files are emitted to `out/`. No base path config needed — the site is served from the custom domain's root by every host (local preview, Vercel, and GitHub Pages via `public/CNAME`) alike.

## Branded assets (favicon, apple touch icon, OG image)

Pre-rendered PNGs in `public/` (`favicon.png`, `apple-icon.png`, `og-image.png`), generated from `scripts/gen-images.mjs` using `next/og`'s `ImageResponse`. Re-run it whenever the brand mark or OG copy changes:

```bash
node scripts/gen-images.mjs
```

Static files rather than Next's `icon.tsx`/`opengraph-image.tsx` route convention on purpose — those export without a file extension, which plain static hosts like GitHub Pages don't reliably serve with the right content-type.

## Content

All copy and structured data live outside the JSX so they're editable without touching components:

- `src/content/content.ts` — every section's Arabic/English text, WhatsApp message templates, contact info
- `src/content/team.ts` — the 7 team member cards (name/title/avatar are placeholders — replace before launch)

Testimonials (`src/components/Testimonials.tsx`) and team members are explicitly marked as placeholder content that must be replaced with real, verifiable data before the site goes live.

## One-file preview build

`npm run build` first, then:

```bash
node scripts/build-artifact.js [output-path]
```

Bundles `out/` into a single self-contained HTML file (CSS and JS inlined, fonts embedded as base64) for sharing on tools that only accept one file — must be served from the site root (`/`), since the app's routing checks `window.location.pathname`.

## Language

Language state lives in `src/context/LanguageContext.tsx` (persisted to `localStorage`, toggled via the header button). No routing/URL-based locale — the whole page re-renders in the selected language and flips `dir`/`lang` on `<html>`.
