# Altiva Real Estate — Marketing Website

Bilingual (Arabic RTL / English LTR) marketing site for Altiva Real Estate, built with Next.js 14 (App Router), TypeScript, Tailwind CSS, and Framer Motion. Configured for static export (`output: "export"`) so it can be deployed on Vercel, GitHub Pages, or any static host.

**Live:** https://sulaimanqallaf.github.io/-real-estate-website/ (GitHub's default project-page URL — auto-deployed via `.github/workflows/deploy-pages.yml` on every push to `main`)

The site was briefly on the custom domain `altivaproperties.com`, but that domain's DNS was never pointed at GitHub Pages, and GitHub Pages redirects the default URL to a configured custom domain even when that domain doesn't resolve — which made the site unreachable everywhere. Reverted to the default URL so there's always a working link. To switch back to the custom domain once its DNS is configured (A records `185.199.108.153`, `.109.153`, `.110.153`, `.111.153`), see the comment at the top of `next.config.mjs`.

## Development

```bash
npm install
npm run dev
```

## Production build (static export)

```bash
npm run build
```

Static files are emitted to `out/`, prefixed with the `/-real-estate-website` base path (`next.config.mjs`'s `NEXT_PUBLIC_BASE_PATH`) to match GitHub's default project-page URL structure. Every asset reference in the app routes through `withBasePath()` (`src/lib/basePath.ts`) rather than a literal `/` for this reason — a plain `next/image` or absolute `href` silently drops the prefix.

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
- `src/content/projectsData.ts` — the project catalog (see "Managing projects" below)

Testimonials (`src/components/Testimonials.tsx`) and team members are explicitly marked as placeholder content that must be replaced with real, verifiable data before the site goes live.

## Managing projects (mini CMS)

There's no admin panel — the project catalog is one array in `src/content/projectsData.ts`. This is deliberate: it keeps the site a static export (fast, free to host, no server to maintain) while still being editable by anyone comfortable with a text file, without touching any component code.

**To add a new project**, append an object to the `projectsData` array:

```ts
{
  slug: "palm-residences",       // becomes the URL: /projects/palm-residences
  title: { ar: "...", en: "..." },
  location: { ar: "...", en: "..." },
  developer: { ar: "...", en: "..." },
  priceFrom: { amount: 2_000_000, currency: "AED" }, // or null if not yet public
  deliveryDate: { ar: "...", en: "..." },
  paymentPlan: [{ ar: "...", en: "..." }, ...],
  features: [{ ar: "...", en: "..." }, ...],
  gallerySlots: 4,                // number of placeholder tiles until real photos exist
  brochureUrl: null,              // set to a PDF URL under public/ once available
  description: { ar: "...", en: "..." },
}
```

**To change a price, delivery date, or feature list**, edit that project's object directly — no rebuild logic to touch. **To add real photos**, replace `ProjectImagePlaceholder` usage in `src/components/ProjectDetail.tsx`/`Projects.tsx` with actual `<img>` tags once photos exist (they aren't wired to a field yet because there are no real photos to point at).

Every project automatically gets: a card on the homepage `/#projects` grid, and a full detail page at `/projects/<slug>` (gallery, developer, price, payment plan, delivery date, features, brochure, and the lead-inquiry form) — both are generated from this one array via `generateStaticParams()`. After editing, run `npm run build` (or push to `main` — GitHub Actions rebuilds automatically) to publish the change. Also add the new `/projects/<slug>` URL to `public/sitemap.xml` (not automated, since it's a static file for search engines).

## Lead capture → Zoho CRM

The project-inquiry form (`src/components/ProjectInquiryForm.tsx`, embedded on every `/projects/<slug>` page) collects name, phone, budget, and project of interest. It's built to connect directly to **Zoho CRM's Web-to-Lead** feature — a plain HTML form POST that creates a Lead record with no backend required, which matters since this site has no server (`output: "export"`).

**Today**, with Zoho not yet connected, submitting the form opens WhatsApp with the same details pre-filled (same UX as the "Request a Consultation" page at `/consultation`) — so lead capture works from day one even before Zoho is wired up.

**To connect Zoho CRM**, the only file to touch is `src/lib/zoho.ts`:

1. In Zoho CRM: **Setup → Developer Space → Web Forms**, create a Web-to-Lead form for the fields the sales team wants captured.
2. Zoho generates an HTML snippet with a form `action` URL and hidden inputs (`xnQsjsdp`, `xmIwtLD`, etc.) — copy those values into `postUrl` and `hiddenFields` in `zoho.ts`.
3. Confirm the field API names in `zoho.ts`'s `fieldNames` (Last Name, Mobile, Description, Company, Lead Source) match what Zoho's snippet shows — these can vary by org configuration.
4. Set `enabled: true` and redeploy.

Once enabled, the form POSTs straight to Zoho (via a hidden iframe, so the page doesn't navigate away) instead of opening WhatsApp — no other code changes needed. This is also the point to test end-to-end with the Altiva sales team: submit a real inquiry, confirm it lands as a Lead in Zoho, and confirm it routes/assigns to the right person.

## Analytics (Meta Pixel / Google Analytics)

Not yet added — deferred until the site is on the official `altivaproperties.com` domain (already the case) and the client provides a Meta Pixel ID and a GA4 Measurement ID. Once provided, both are a small addition to `src/app/layout.tsx`: a Meta Pixel `<script>` snippet and the GA4 `gtag.js` snippet, each loaded via `next/script` with `strategy="afterInteractive"` so they don't block the initial page render.

## One-file preview build

`npm run build` first, then:

```bash
node scripts/build-artifact.js [output-path]
```

Bundles `out/` into a single self-contained HTML file (CSS and JS inlined, fonts embedded as base64) for sharing on tools that only accept one file — must be served from the site root (`/`), since the app's routing checks `window.location.pathname`.

## Language

Language state lives in `src/context/LanguageContext.tsx` (persisted to `localStorage`, toggled via the header button). No routing/URL-based locale — the whole page re-renders in the selected language and flips `dir`/`lang` on `<html>`.
