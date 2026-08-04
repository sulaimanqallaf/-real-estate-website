export type Building = {
  width: number;
  height: number;
  spireHeight?: number;
};

// Stylized skyline silhouette (not a literal rendering) — a tall central
// spire echoes the Burj Khalifa without claiming photographic accuracy.
export const dubaiSkylineBuildings: Building[] = [
  { width: 30, height: 18 },
  { width: 22, height: 34 },
  { width: 28, height: 22 },
  { width: 18, height: 48 },
  { width: 26, height: 30 },
  { width: 20, height: 58 },
  { width: 24, height: 26 },
  { width: 16, height: 70, spireHeight: 34 },
  { width: 24, height: 40 },
  { width: 20, height: 24 },
  { width: 30, height: 52 },
  { width: 22, height: 32 },
  { width: 28, height: 20 },
  { width: 24, height: 44 },
  { width: 20, height: 28 },
  { width: 26, height: 16 },
];

export const roiChartPoints: [number, number][] = [
  [0, 0.85],
  [0.12, 0.72],
  [0.22, 0.78],
  [0.34, 0.55],
  [0.46, 0.62],
  [0.58, 0.38],
  [0.7, 0.44],
  [0.82, 0.2],
  [1, 0.06],
];

export function buildSkylinePath(buildings: Building[], viewHeight: number) {
  const baseY = viewHeight;
  let x = 0;
  let d = `M0,${baseY}`;
  let currentTopY = baseY;

  buildings.forEach((b) => {
    const topY = baseY - b.height;
    d += ` L${x},${topY}`;
    if (b.spireHeight) {
      const spireX = x + b.width / 2;
      d += ` L${spireX - 1},${topY} L${spireX},${topY - b.spireHeight} L${spireX + 1},${topY}`;
    }
    d += ` L${x + b.width},${topY}`;
    x += b.width;
    currentTopY = topY;
  });

  d += ` L${x},${baseY}`;
  return { d, totalWidth: x, endY: currentTopY };
}

export function buildChartPath(
  points: [number, number][],
  viewWidth: number,
  viewHeight: number
) {
  return points
    .map(([px, py], i) => {
      const x = px * viewWidth;
      const y = (1 - py) * viewHeight;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}
