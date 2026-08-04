"use client";

import { motion, useReducedMotion } from "framer-motion";
import { ChevronDown } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";
import { hero, whatsappLink } from "@/content/content";
import { t } from "@/lib/i18n";
import { SkylineBackdrop } from "./Placeholder";
import { SectionRule } from "./SectionRule";
import { WhatsappCta } from "./WhatsappCta";

export function Hero() {
  const { locale } = useLanguage();
  const shouldReduceMotion = useReducedMotion();

  return (
    <section
      id="home"
      className="relative flex min-h-screen items-center overflow-hidden pt-24"
    >
      <SkylineBackdrop />

      <div className="relative mx-auto max-w-4xl px-5 py-16 text-center sm:px-8">
        <motion.div
          initial={shouldReduceMotion ? undefined : { opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: "easeOut" }}
        >
          <SectionRule className="mb-7" />
        </motion.div>

        <motion.h1
          initial={shouldReduceMotion ? undefined : { opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, ease: "easeOut", delay: 0.05 }}
          className="font-display-heading text-balance text-4xl font-bold leading-[1.1] text-cream sm:text-6xl md:text-7xl"
        >
          {t(hero.title, locale)}
        </motion.h1>

        <motion.p
          initial={shouldReduceMotion ? undefined : { opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, ease: "easeOut", delay: 0.15 }}
          className="mx-auto mt-6 max-w-2xl text-balance text-base leading-relaxed text-cream/80 sm:text-lg"
        >
          {t(hero.subtitle, locale)}
        </motion.p>

        <motion.div
          initial={shouldReduceMotion ? undefined : { opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, ease: "easeOut", delay: 0.3 }}
          className="mt-10 flex justify-center"
        >
          <WhatsappCta href={whatsappLink(hero.ctaMessage, locale)}>
            {t(hero.cta, locale)}
          </WhatsappCta>
        </motion.div>
      </div>

      <div className="absolute inset-x-0 bottom-8 flex flex-col items-center gap-1 text-cream/50">
        <span className="text-xs">{t(hero.scrollHint, locale)}</span>
        <ChevronDown className="h-4 w-4" aria-hidden="true" />
      </div>
    </section>
  );
}
