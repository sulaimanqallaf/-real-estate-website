// One-off generator for branded static image assets (favicon, apple
// touch icon, OG card) — run manually when the brand mark changes, not
// on every build, so CI doesn't depend on this.
//
// Uses the real Altiva icon mark (public/brand/altiva-icon.png,
// extracted from the client's Instagram profile photo) composited onto
// the brand navy via satori <img>, rather than a hand-drawn stand-in.
//
// next/og's ImageResponse takes a satori element tree, which is just
// plain { type, props } objects — no JSX/React needed to build one.
import { ImageResponse } from "next/og.js";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";

const outDir = path.join(process.cwd(), "public");
await mkdir(outDir, { recursive: true });

const el = (type, props, children) => ({ type, props: { ...props, children } });

const iconBuf = await readFile(path.join(outDir, "brand/altiva-icon.png"));
const iconDataUri = `data:image/png;base64,${iconBuf.toString("base64")}`;
// Source is 245x317 (w x h) — keep that aspect ratio at every size.
const ICON_RATIO = 317 / 245;

async function save(name, node, size) {
  const res = new ImageResponse(node, size);
  const buf = Buffer.from(await res.arrayBuffer());
  await writeFile(path.join(outDir, name), buf);
  console.log("wrote", name, buf.length, "bytes");
}

function markIcon(iconHeight) {
  return el(
    "div",
    {
      style: {
        width: "100%",
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#0A0F1E",
      },
    },
    el("img", {
      src: iconDataUri,
      width: Math.round(iconHeight / ICON_RATIO),
      height: iconHeight,
    })
  );
}

await save("favicon.png", markIcon(32), { width: 48, height: 48 });
await save("apple-icon.png", markIcon(120), { width: 180, height: 180 });

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
      background: "linear-gradient(180deg, #050710 0%, #0A0F1E 55%, #16213D 100%)",
    },
  },
  [
    el("img", {
      src: iconDataUri,
      width: Math.round(120 / ICON_RATIO),
      height: 120,
      style: { marginBottom: 18 },
    }),
    el("div", { style: { display: "flex", alignItems: "baseline", gap: 18 } }, [
      el(
        "span",
        { style: { fontSize: 88, fontWeight: 700, letterSpacing: 6, color: "#F7F4EE" } },
        "ALTIVA"
      ),
      el(
        "span",
        { style: { fontSize: 30, fontWeight: 600, color: "#F0D68C" } },
        "Real Estate"
      ),
    ]),
    el(
      "div",
      { style: { marginTop: 24, fontSize: 28, color: "rgba(247,244,238,0.75)" } },
      "Invest in Dubai from Kuwait — returns from 8% annually"
    ),
    el("div", {
      style: {
        display: "flex",
        marginTop: 48,
        width: 640,
        height: 3,
        background: "linear-gradient(90deg, #7A5A22 0%, #F0D68C 50%, #7A5A22 100%)",
        borderRadius: 999,
      },
    }),
  ]
);

await save("og-image.png", ogImage, { width: 1200, height: 630 });
