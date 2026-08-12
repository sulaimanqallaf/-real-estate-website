"use client";

import { motion, useReducedMotion } from "framer-motion";
import type { ReactNode } from "react";

// Runs on every route change (App Router re-mounts this per navigation),
// giving page-to-page transitions a soft fade/rise instead of an abrupt
// cut — the same restrained motion language as ScrollReveal, just at the
// page level.
export default function Template({ children }: { children: ReactNode }) {
  const shouldReduceMotion = useReducedMotion();

  return (
    <motion.div
      initial={shouldReduceMotion ? undefined : { opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, ease: "easeOut" }}
    >
      {children}
    </motion.div>
  );
}
