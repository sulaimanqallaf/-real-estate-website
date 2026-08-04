// One-off generator for branded static image assets (favicon, apple
// touch icon, OG card) — run manually when the brand mark changes, not
// on every build, so CI doesn't depend on this.
//
// next/og's ImageResponse takes a satori element tree, which is just
// plain { type, props } objects — no JSX/React needed to build one.
import { ImageResponse } from "next/og.js";
import { writeFile, mkdir } from "node:fs/promises";
import path from "node:path";

const outDir = path.join(process.cwd(), "public");
await mkdir(outDir, { recursive: true });

const el = (type, props, children) => ({ type, props: { ...props, children } });

const spirePath =
  "M2 24 L2 18 L7 18 L7 14 L12 14 L12 20 L15 20 L15 6 L15.5 3 L16 6 L16 20 L19 20 L19 16 L24 16 L24 22 L28 22 L28 24 Z";

async function save(name, node, size) {
  const res = new ImageResponse(node, size);
  const buf = Buffer.from(await res.arrayBuffer());
  await writeFile(path.join(outDir, name), buf);
  console.log("wrote", name, buf.length, "bytes");
}

function markIcon(svgSize) {
  return el(
    "div",
    {
      style: {
        width: "100%",
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#0B1220",
      },
    },
    el(
      "svg",
      { width: svgSize, height: svgSize, viewBox: "0 0 30 30" },
      el("path", { d: spirePath, fill: "#E8C77E" })
    )
  );
}

await save("favicon.png", markIcon(30), { width: 48, height: 48 });
await save("apple-icon.png", markIcon(112), { width: 180, height: 180 });

const ogImage = el(
  "div",
  {
    style: {
      width: "100%",
      height: "100%",
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      background: "linear-gradient(180deg, #070B14 0%, #0B1220 55%, #141F35 100%)",
    },
  },
  [
    el("div", { style: { display: "flex", alignItems: "baseline", gap: 18 } }, [
      el(
        "span",
        { style: { fontSize: 108, fontWeight: 700, letterSpacing: 6, color: "#F7F4EE" } },
        "ALTIVA"
      ),
      el(
        "span",
        { style: { fontSize: 34, fontWeight: 600, color: "#E8C77E" } },
        "Real Estate"
      ),
    ]),
    el(
      "div",
      { style: { marginTop: 28, fontSize: 30, color: "rgba(247,244,238,0.75)" } },
      "Invest in Dubai from Kuwait — returns from 8% annually"
    ),
    el("div", {
      style: {
        display: "flex",
        marginTop: 56,
        width: 640,
        height: 3,
        background: "linear-gradient(90deg, #C9A15A 0%, #E8C77E 50%, #C9A15A 100%)",
        borderRadius: 999,
      },
    }),
  ]
);

await save("og-image.png", ogImage, { width: 1200, height: 630 });
