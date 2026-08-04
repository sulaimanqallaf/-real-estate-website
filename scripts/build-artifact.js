// Produces a single self-contained HTML file from the static export in
// out/, for previewing on external tools that only accept one file
// (fonts/CSS/JS all inlined, no /_next/* references left).
const fs = require("fs");
const path = require("path");

const OUT_DIR = path.join(__dirname, "..", "out");
const DEST =
  process.argv[2] ||
  "/tmp/claude-0/-home-user-unified-investment-manager/6a4005a4-da01-5fc7-b0d2-4653e6ae3bca/scratchpad/altiva-preview.html";

let html = fs.readFileSync(path.join(OUT_DIR, "index.html"), "utf8");

// 1. Inline CSS, with fonts converted to base64 data URIs.
const cssPath = path.join(OUT_DIR, "_next/static/css/fcbf01c1718f0380.css");
let css = fs.readFileSync(cssPath, "utf8");
css = css.replace(/url\(\/_next\/static\/media\/([^)]+)\)/g, (m, file) => {
  const fontPath = path.join(OUT_DIR, "_next/static/media", file);
  const data = fs.readFileSync(fontPath).toString("base64");
  return `url(data:font/woff2;base64,${data})`;
});

// Replacer functions (not strings) — a plain-string replacement is subject
// to $-pattern substitution ($&, $', $$, ...) and this bundle's minified
// code contains literal "$&"/"$'" sequences that would otherwise corrupt it.
html = html.replace(
  /<link rel="stylesheet" href="\/_next\/static\/css\/[^"]+"[^>]*\/>/,
  () => `<style>${css}</style>`
);

// 2. Drop preload links (fonts/scripts) — nothing external to fetch anymore.
html = html.replace(/<link rel="preload"[^>]*>/g, "");

// 3. Drop favicon link (handled via artifact favicon param instead).
html = html.replace(/<link rel="icon"[^>]*>/g, "");

// 4. Inline every JS chunk referenced by <script src="...">, skipping the
// legacy noModule polyfill bundle (irrelevant for any modern browser, and
// dropping it removes the only script requiring special module handling).
const chunkFiles = [
  "_next/static/chunks/webpack-074104555580e58d.js",
  "_next/static/chunks/framework-f66176bb897dc684.js",
  "_next/static/chunks/main-88c90d9537422e3e.js",
  "_next/static/chunks/main-app-2199670a3c1d52cc.js",
  "_next/static/chunks/117-e7a95ff872d9ad6e.js",
  "_next/static/chunks/590-b473301e67022b79.js",
  "_next/static/chunks/fd9d1056-1f1f859026f5f0fa.js",
  "_next/static/chunks/app/layout-fac6d4f57374d61c.js",
  "_next/static/chunks/app/page-349c85ce2f3e8ba6.js",
  "_next/static/chunks/pages/_app-72b849fbd24ac258.js",
  "_next/static/chunks/pages/_error-7ba65e1336b92748.js",
];

const bundled = chunkFiles
  .map((f) => fs.readFileSync(path.join(OUT_DIR, f), "utf8"))
  .join("\n;\n")
  // A literal "</script" inside the JS source would prematurely close the
  // wrapping <script> tag when parsed as HTML.
  .replace(/<\/script/gi, "<\\/script");

// Remove the original script tags (both the <script src> ones and the
// noModule polyfill tag).
html = html.replace(/<script src="\/_next\/static\/chunks\/[^"]+"[^>]*><\/script>/g, "");

// Insert the bundled scripts right before </body>.
html = html.replace(
  "</body>",
  () => `<script>\n${bundled}\n</script>\n</body>`
);

fs.mkdirSync(path.dirname(DEST), { recursive: true });
fs.writeFileSync(DEST, html);
console.log("Wrote", DEST, `(${(html.length / 1024 / 1024).toFixed(2)} MB)`);
