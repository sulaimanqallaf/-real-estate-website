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
          style={{ filter: "url(#skyline-glow)" }}
          initial={shouldReduceMotion ? undefined : { pathLength: 0 }}
          whileInView={shouldReduceMotion ? undefined : { pathLength: 1 }}
          viewport={{ once: true, margin: "-40px" }}
          transition={{ duration: 1.8, ease: "easeInOut" }}
        />
        <defs>
          <linearGradient id="skyline-copper" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#7A5A22" stopOpacity="0.5" />
            <stop offset="50%" stopColor="#F0D68C" stopOpacity="0.95" />
            <stop offset="100%" stopColor="#7A5A22" stopOpacity="0.5" />
          </linearGradient>
          <filter id="skyline-glow" x="-20%" y="-200%" width="140%" height="500%">
            <feGaussianBlur stdDeviation="1.2" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
      </svg>
    </div>
  );
}
