"use client";

import { motion, useReducedMotion, useScroll, useTransform } from "framer-motion";
import { useMemo, useRef } from "react";
import {
  buildChartPath,
  buildSkylinePath,
  dubaiSkylineBuildings,
  roiChartPoints,
} from "@/lib/skyline";

const VIEW_WIDTH = 600;
const VIEW_HEIGHT = 90;

export function SkylineToChart({ className = "" }: { className?: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const shouldReduceMotion = useReducedMotion();

  const { scrollYProgress } = useScroll({
    target: containerRef,
    offset: ["start 0.85", "start 0.25"],
  });

  const skylineOpacity = useTransform(scrollYProgress, [0, 1], [1, 0]);
  const chartOpacity = useTransform(scrollYProgress, [0, 1], [0, 1]);
  const chartDraw = useTransform(scrollYProgress, [0, 1], [0.15, 1]);

  const skyline = useMemo(
    () => buildSkylinePath(dubaiSkylineBuildings, VIEW_HEIGHT),
    []
  );
  const chartPath = useMemo(
    () => buildChartPath(roiChartPoints, VIEW_WIDTH, VIEW_HEIGHT * 0.8),
    []
  );

  return (
    <div ref={containerRef} className={`w-full overflow-hidden ${className}`} aria-hidden="true">
      <svg
        viewBox={`0 0 ${skyline.totalWidth} ${VIEW_HEIGHT}`}
        preserveAspectRatio="none"
        className="h-16 w-full md:h-20"
      >
        <defs>
          <linearGradient id="skyline-chart-copper" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#7A5A22" stopOpacity="0.5" />
            <stop offset="50%" stopColor="#F0D68C" stopOpacity="0.95" />
            <stop offset="100%" stopColor="#7A5A22" stopOpacity="0.5" />
          </linearGradient>
        </defs>
        <motion.path
          d={skyline.d}
          fill="none"
          stroke="url(#skyline-chart-copper)"
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
          style={shouldReduceMotion ? undefined : { opacity: skylineOpacity }}
        />
      </svg>
      <svg
        viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
        preserveAspectRatio="none"
        className="-mt-16 h-16 w-full md:-mt-20 md:h-20"
      >
        <motion.path
          d={chartPath}
          fill="none"
          stroke="url(#skyline-chart-copper)"
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
          style={
            shouldReduceMotion
              ? undefined
              : { opacity: chartOpacity, pathLength: chartDraw }
          }
        />
      </svg>
    </div>
  );
}
