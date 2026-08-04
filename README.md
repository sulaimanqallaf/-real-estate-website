# Altiva Real Estate — Marketing Website

Bilingual (Arabic RTL / English LTR) marketing site for Altiva Real Estate, built with Next.js 14 (App Router), TypeScript, Tailwind CSS, and Framer Motion. Configured for static export (`output: "export"`) so it can be deployed on Vercel or any static host.

## Development

```bash
npm install
npm run dev
```

## Production build (static export)

```bash
npm run build
```

Static files are emitted to `out/`.

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
