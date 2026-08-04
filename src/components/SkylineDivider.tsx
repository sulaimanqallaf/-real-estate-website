"use client";

import { motion, useReducedMotion } from "framer-motion";
import { useMemo } from "react";
import { buildSkylinePath, dubaiSkylineBuildings } from "@/lib/skyline";

const VIEW_HEIGHT = 90;

export function SkylineDivider({ className = "" }: { className?: string }) {
  const shouldReduceMotion = useReducedMotion();
  const { d, totalWidth } = useMemo(
    () => buildSkylinePath(dubaiSkylineBuildings, VIEW_HEIGHT),
    []
  );

  return (
    <div className={`w-full overflow-hidden ${className}`} aria-hidden="true">
      <svg
        viewBox={`0 0 ${totalWidth} ${VIEW_HEIGHT}`}
        preserveAspectRatio="none"
        className="h-16 w-full md:h-20"
      >
        <motion.path
          d={d}
          fill="none"
          stroke="url(#skyline-copper)"
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
          initial={shouldReduceMotion ? undefined : { pathLength: 0 }}
          whileInView={shouldReduceMotion ? undefined : { pathLength: 1 }}
          viewport={{ once: true, margin: "-40px" }}
          transition={{ duration: 1.8, ease: "easeInOut" }}
        />
        <defs>
          <linearGradient id="skyline-copper" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#C9A15A" stopOpacity="0.55" />
            <stop offset="50%" stopColor="#E8C77E" stopOpacity="0.9" />
            <stop offset="100%" stopColor="#C9A15A" stopOpacity="0.55" />
          </linearGradient>
        </defs>
      </svg>
    </div>
  );
}
