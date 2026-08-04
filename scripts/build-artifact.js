// Produces a single self-contained HTML file from the static export in
// out/, for previewing on external tools that only accept one file
// (fonts/CSS/JS all inlined, no /_next/* references left).
//
// Filename-agnostic by design: Next.js content-hashes every chunk/CSS
// file, so those hashes change on every build. This discovers the real
// filenames from out/ instead of hardcoding them.
const fs = require("fs");
const path = require("path");

const OUT_DIR = path.join(__dirname, "..", "out");
const DEST =
  process.argv[2] ||
  "/tmp/claude-0/-home-user-unified-investment-manager/6a4005a4-da01-5fc7-b0d2-4653e6ae3bca/scratchpad/altiva-preview.html";

let html = fs.readFileSync(path.join(OUT_DIR, "index.html"), "utf8");

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? walk(full) : [full];
  });
}

// 1. Inline CSS, with fonts converted to base64 data URIs.
const cssDir = path.join(OUT_DIR, "_next/static/css");
const cssFiles = fs.existsSync(cssDir) ? fs.readdirSync(cssDir).filter((f) => f.endsWith(".css")) : [];
let css = cssFiles
  .map((f) => fs.readFileSync(path.join(cssDir, f), "utf8"))
  .join("\n");
css = css.replace(/url\(\/_next\/static\/media\/([^)]+)\)/g, (m, file) => {
  const fontPath = path.join(OUT_DIR, "_next/static/media", file);
  const data = fs.readFileSync(fontPath).toString("base64");
  return `url(data:font/woff2;base64,${data})`;
});

// Replacer functions (not strings) — a plain-string replacement is subject
// to $-pattern substitution ($&, $', $$, ...) and this bundle's minified
// code contains literal "$&"/"$'" sequences that would otherwise corrupt it.
html = html.replace(
  /<link rel="stylesheet" href="\/_next\/static\/css\/[^"]+"[^>]*\/>/g,
  () => ""
);
html = html.replace("</head>", () => `<style>${css}</style></head>`);

// 2. Drop preload links (fonts/scripts) — nothing external to fetch anymore.
html = html.replace(/<link rel="preload"[^>]*>/g, "");

// 3. Drop icon links — favicon/apple-touch-icon point at paths that don't
// exist in a single-file bundle; the Artifact tool's own favicon param
// covers the tab icon instead.
html = html.replace(/<link rel="icon"[^>]*>/g, "");
html = html.replace(/<link rel="apple-touch-icon"[^>]*>/g, "");

// 4. Inline every JS chunk under _next/static/chunks, skipping the legacy
// noModule polyfill bundle (irrelevant for any modern browser, and
// dropping it removes the only script requiring special module handling).
// Execution order matters despite webpack's chunk-registration array
// trick: the webpack runtime, then framework, then main (Next's client
// bootstrap) must run before main-app's entry, or main-app's top-level
// code touches window.next before main has set it up — throws "Cannot
// set property router of #<Object> which has only a getter". Pure
// alphabetical sort put main-app before main and broke exactly this.
function chunkPriority(file) {
  const base = path.basename(file);
  if (base.startsWith("webpack-")) return 0;
  if (base.startsWith("framework-")) return 1;
  if (base.startsWith("main-app-")) return 3;
  if (base.startsWith("main-")) return 2;
  return 4;
}

const chunksDir = path.join(OUT_DIR, "_next/static/chunks");
const chunkFiles = walk(chunksDir)
  .filter((f) => f.endsWith(".js") && !path.basename(f).startsWith("polyfills-"))
  .sort((a, b) => chunkPriority(a) - chunkPriority(b) || a.localeCompare(b));

const bundled = chunkFiles
  .map((f) => fs.readFileSync(f, "utf8"))
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
console.log("CSS files:", cssFiles.length, "JS chunks:", chunkFiles.length);
